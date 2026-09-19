"""Разбор путей Яндекс.Диска → продукт / урок / модуль / роль файла."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional, Sequence

from course.products import (
    EXPERT_DISK_FOLDER,
    EXPERT_PRODUCT_ID,
    ProductRegistry,
    folder_to_product_id,
    get_registry,
)

LESSON_DIR_RE = re.compile(
    r"^Урок\s*(\d{1,2})(?:\s*[._]\s*(\d{1,2}))?\s*[.\-–—:]?\s*(.*)$",
    re.IGNORECASE,
)
MODULE_DIR_RE = re.compile(
    r"^Модуль\s*(\d{1,2})\b(.*)$",
    re.IGNORECASE,
)
LESSON_KEY_IN_NAME_RE = re.compile(
    r"(?:урок|практик[аи]|zoom)?\s*(\d{1,2})(?:\s*[._]\s*(\d{1,2}))?",
    re.IGNORECASE,
)
DATE_IN_NAME_RE = re.compile(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})")

DOC_EXTS = frozenset({".pdf", ".docx", ".txt", ".md"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"})
AUDIO_EXTS = frozenset({".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac"})
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

IGNORED_DIR_NAMES = frozenset({"архив", "archive"})


@dataclass(frozen=True)
class DiskFileRole:
    product_id: str
    kind: str
    lesson_key: str = ""
    lesson_title: str = ""
    module_no: Optional[int] = None
    lesson_no: Optional[int] = None
    platform: str = ""
    recorded_on: Optional[date] = None
    needs_lesson_confirm: bool = False
    skip: bool = False
    skip_reason: str = ""


def _norm_parts(remote_path: str) -> List[str]:
    p = (remote_path or "").strip().replace("\\", "/")
    return [seg for seg in p.split("/") if seg and seg not in (".",)]


def is_ignored_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if n.startswith("_") or n.startswith("."):
        return True
    return n.casefold() in IGNORED_DIR_NAMES


def parse_lesson_folder(name: str) -> Optional[tuple[str, Optional[int], int, str]]:
    """Возвращает (lesson_key, module_no, lesson_no, title) или None."""
    m = LESSON_DIR_RE.match((name or "").strip())
    if not m:
        return None
    a = int(m.group(1))
    b = m.group(2)
    title = (m.group(3) or "").strip(" .-–—:_")
    if b is not None:
        lesson_no = int(b)
        return f"{a}.{lesson_no}", a, lesson_no, title
    return str(a), None, a, title


def parse_module_folder(name: str) -> Optional[int]:
    m = MODULE_DIR_RE.match((name or "").strip())
    if not m:
        return None
    return int(m.group(1))


def parse_lesson_key_from_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.search(
        r"урок\s*[._-]?\s*(\d{1,2})(?:\s*[._-]\s*(\d{1,2}))?",
        raw,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(r"(?<!\d)(\d{1,2})[._](\d{1,2})(?!\d)", raw)
        if not m:
            return ""
        return f"{int(m.group(1))}.{int(m.group(2))}"
    a = int(m.group(1))
    if m.group(2):
        return f"{a}.{int(m.group(2))}"
    return str(a)


def parse_date_from_name(name: str) -> Optional[date]:
    m = DATE_IN_NAME_RE.search(name or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _section_of(parts: Sequence[str], after_product: int) -> str:
    """Имя папки первого уровня внутри продукта: Курс / Практики / Эфиры / …"""
    if after_product < 0 or after_product >= len(parts):
        return ""
    return parts[after_product]


def classify_disk_path(
    remote_path: str,
    *,
    active_product_id: str,
    registry: Optional[ProductRegistry] = None,
    course_disk_root: str = "/Аватар",
) -> DiskFileRole:
    """Разбирает путь файла на Диске относительно COURSE_DISK_ROOT."""
    parts = _norm_parts(remote_path)
    root_parts = _norm_parts(course_disk_root)
    if root_parts and parts[: len(root_parts)] == root_parts:
        parts = parts[len(root_parts) :]
    if not parts:
        return DiskFileRole(product_id="", kind="other", skip=True, skip_reason="empty")

    name = parts[-1]
    if is_ignored_name(name):
        return DiskFileRole(product_id="", kind="other", skip=True, skip_reason="hidden")

    for seg in parts[:-1]:
        if is_ignored_name(seg) or seg.casefold() in IGNORED_DIR_NAMES:
            return DiskFileRole(product_id="", kind="other", skip=True, skip_reason="archive")

    top = parts[0]
    pid = folder_to_product_id(top) if registry is None else None
    if registry is not None:
        if top.casefold().startswith("00") and "эксперт" in top.casefold():
            pid = EXPERT_PRODUCT_ID
        else:
            for p in registry.products:
                if p.disk_folder.casefold() == top.casefold():
                    pid = p.id
                    break
    if pid is None:
        pid = folder_to_product_id(top)

    if not pid:
        return DiskFileRole(product_id="", kind="other", skip=True, skip_reason="unknown_folder")

    if pid != EXPERT_PRODUCT_ID and pid != active_product_id:
        return DiskFileRole(product_id=pid, kind="other", skip=True, skip_reason="inactive_product")

    ext = Path(name).suffix.lower()
    stem = Path(name).stem
    rest = parts[1:]

    # Об эксперте / О продукте
    if pid == EXPERT_PRODUCT_ID:
        if rest and rest[0].casefold().startswith("пост"):
            platform = rest[1].strip().lower() if len(rest) > 1 else ""
            if ext in DOC_EXTS:
                return DiskFileRole(
                    product_id=EXPERT_PRODUCT_ID,
                    kind="post",
                    platform=_platform_from_folder(platform),
                )
            return DiskFileRole(product_id=pid, kind="other", skip=True, skip_reason="unsupported")
        if stem.casefold().startswith("об эксперт") and ext in DOC_EXTS:
            return DiskFileRole(product_id=EXPERT_PRODUCT_ID, kind="expert_info")
        if ext not in DOC_EXTS:
            return DiskFileRole(product_id=pid, kind="other", skip=True, skip_reason="unsupported")
        return DiskFileRole(product_id=EXPERT_PRODUCT_ID, kind="extra")

    if rest and rest[0].casefold().startswith("пост"):
        platform = rest[1].strip().lower() if len(rest) > 1 else ""
        if ext in DOC_EXTS:
            return DiskFileRole(
                product_id=pid,
                kind="post",
                platform=_platform_from_folder(platform),
            )
        return DiskFileRole(product_id=pid, kind="other", skip=True, skip_reason="unsupported")

    if stem.casefold().startswith("о продукт") and ext in DOC_EXTS:
        return DiskFileRole(product_id=pid, kind="product_info")

    lesson_key = ""
    lesson_title = ""
    module_no: Optional[int] = None
    lesson_no: Optional[int] = None
    in_lesson_folder = False

    for i, seg in enumerate(parts[:-1]):
        mod = parse_module_folder(seg)
        if mod is not None:
            module_no = mod
        parsed = parse_lesson_folder(seg)
        if parsed:
            lesson_key, parsed_mod, lesson_no, lesson_title = parsed
            if parsed_mod is None and module_no is not None:
                lesson_key = f"{module_no}.{lesson_no}"
                parsed_mod = module_no
            module_no = parsed_mod if parsed_mod is not None else module_no
            in_lesson_folder = True

    section = rest[0].casefold() if rest else ""
    recorded_on = parse_date_from_name(stem)

    if ext in MEDIA_EXTS:
        kind = "other"
        needs_confirm = False
        if in_lesson_folder:
            kind = "lesson_video"
        elif "практик" in section:
            kind = "practice"
            if not lesson_key:
                lesson_key = parse_lesson_key_from_text(stem)
            if not lesson_key:
                needs_confirm = True
        elif "эфир" in section:
            kind = "broadcast"
            if not lesson_key:
                lesson_key = parse_lesson_key_from_text(stem)
        elif "друг" in section:
            kind = "other"
        else:
            kind = "other"
            if not lesson_key:
                lesson_key = parse_lesson_key_from_text(stem)
        return DiskFileRole(
            product_id=pid,
            kind=kind,
            lesson_key=lesson_key,
            lesson_title=lesson_title,
            module_no=module_no,
            lesson_no=lesson_no,
            recorded_on=recorded_on,
            needs_lesson_confirm=needs_confirm,
        )

    if ext not in DOC_EXTS:
        return DiskFileRole(product_id=pid, kind="other", skip=True, skip_reason="unsupported")

    lname = name.casefold()
    if in_lesson_folder:
        if "конспект" in lname:
            kind = "summary"
        elif "слайд" in lname or "презентац" in lname:
            kind = "slides"
        else:
            kind = "extra"
        return DiskFileRole(
            product_id=pid,
            kind=kind,
            lesson_key=lesson_key,
            lesson_title=lesson_title,
            module_no=module_no,
            lesson_no=lesson_no,
        )

    if "практик" in section:
        lk = parse_lesson_key_from_text(stem) or lesson_key
        return DiskFileRole(
            product_id=pid,
            kind="practice",
            lesson_key=lk,
            recorded_on=recorded_on,
            needs_lesson_confirm=not lk,
        )
    if "эфир" in section:
        return DiskFileRole(
            product_id=pid,
            kind="broadcast",
            lesson_key=parse_lesson_key_from_text(stem),
            recorded_on=recorded_on,
        )

    return DiskFileRole(product_id=pid, kind="extra", lesson_key=lesson_key)


def _platform_from_folder(name: str) -> str:
    n = (name or "").strip().casefold()
    aliases = {
        "telegram": "telegram",
        "tg": "telegram",
        "instagram": "instagram",
        "ig": "instagram",
        "insta": "instagram",
        "vk": "vk",
        "вк": "vk",
        "youtube": "youtube",
        "yt": "youtube",
        "threads": "threads",
        "facebook": "facebook",
        "fb": "facebook",
    }
    return aliases.get(n, n)


def lesson_sort_key(lesson_key: str) -> tuple[int, int]:
    parts = (lesson_key or "").split(".")
    try:
        a = int(parts[0])
    except (ValueError, IndexError):
        a = 0
    try:
        b = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        b = 0
    return a, b
