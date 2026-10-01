"""Файлы и сообщения со ссылками на записи (Kinescope / Vimeo / YouTube / Zoom)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from course.disk_layout import parse_lesson_key_from_text, parse_module_folder
from course.video_hosts.ytdlp import extract_video_urls

_PASS_RE = re.compile(
    r"(?:пароль|password|passcode|pwd)\s*[=:\-–—]?\s*(\S+)",
    re.IGNORECASE,
)
_TYPE_RE = re.compile(
    r"(?:тип|kind)\s*[:\-–—]\s*(урок|практик\w*|эфир|другое|lesson_video|practice|broadcast|other)",
    re.IGNORECASE,
)
_LESSON_LINE_RE = re.compile(
    r"(?:урок|lesson)\s*[:\-–—]?\s*(\d{1,2}(?:[._]\d{1,2})?)",
    re.IGNORECASE,
)
_MODULE_LINE_RE = re.compile(
    r"(?:модул[ьяе]|раздел[ае]?|module)\s*[:\-–—]?\s*(\d{1,2})",
    re.IGNORECASE,
)


_DESC_RE = re.compile(
    r"^(?:описание|description|desc)\s*[:\-–—]\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class VideoLinkEntry:
    host: str
    url: str
    password: str = ""
    kind: str = ""
    lesson_key: str = ""
    module_no: Optional[int] = None
    description: str = ""


def extract_video_password(text: str) -> str:
    m = _PASS_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).strip().strip(".,;)»\"'")


def _norm_kind(raw: str) -> str:
    n = (raw or "").casefold()
    if n.startswith("урок") or n == "lesson_video":
        return "lesson_video"
    if n.startswith("практик") or n == "practice":
        return "practice"
    if n.startswith("эфир") or n == "broadcast":
        return "broadcast"
    if n.startswith("друг") or n == "other":
        return "other"
    return ""


def extract_video_description(text: str) -> str:
    """Текст после «описание:» до ссылки или следующей служебной строки."""
    parts: List[str] = []
    collecting = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        dm = _DESC_RE.match(line)
        if dm:
            collecting = True
            rest = (dm.group(1) or "").strip()
            if rest:
                parts.append(rest)
            continue
        if not collecting:
            continue
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if extract_video_urls(line) or _is_meta_line(line):
            break
        parts.append(line)
    return "\n".join(parts).strip()[:4000]


def is_password_followup(text: str) -> bool:
    """Сообщение только с паролем, без ссылки на видео."""
    blob = (text or "").strip()
    if not blob or extract_video_urls(blob):
        return False
    if not extract_video_password(blob):
        return False
    return len(blob) <= 240


def _is_meta_line(line: str) -> bool:
    if _TYPE_RE.match(line) or _DESC_RE.match(line):
        return True
    if "http" in line.casefold():
        return False
    if _MODULE_LINE_RE.match(line) or _LESSON_LINE_RE.match(line):
        return True
    if _PASS_RE.match(line):
        return True
    return False


def _apply_description(entries: List[VideoLinkEntry], description: str) -> None:
    desc = (description or "").strip()[:4000]
    if not desc:
        return
    empty = [e for e in entries if not e.description]
    if empty:
        for e in empty:
            e.description = desc
        return
    if entries:
        entries[-1].description = desc


def parse_video_link_file(text: str) -> List[VideoLinkEntry]:
    """Формат файла запись.txt — см. тесты и подсказку в /help."""
    kind = ""
    lesson_key = ""
    module_no: Optional[int] = None
    password = ""
    description = ""
    desc_parts: List[str] = []
    collecting_desc = False
    out: List[VideoLinkEntry] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        dm = _DESC_RE.match(line)
        if dm:
            collecting_desc = True
            rest = (dm.group(1) or "").strip()
            desc_parts = [rest] if rest else []
            description = "\n".join(desc_parts).strip()[:4000]
            _apply_description(out, description)
            continue
        if collecting_desc and not extract_video_urls(line) and not (
            _TYPE_RE.match(line)
            or (_MODULE_LINE_RE.match(line) and "http" not in line.casefold())
            or (_LESSON_LINE_RE.match(line) and "http" not in line.casefold())
            or (_PASS_RE.match(line) and "http" not in line.casefold())
        ):
            desc_parts.append(line)
            description = "\n".join(desc_parts).strip()[:4000]
            _apply_description(out, description)
            continue
        collecting_desc = False
        tm = _TYPE_RE.match(line)
        if tm:
            kind = _norm_kind(tm.group(1))
            continue
        mm = _MODULE_LINE_RE.match(line)
        if mm and "http" not in line.casefold():
            module_no = int(mm.group(1))
            continue
        lm = _LESSON_LINE_RE.match(line)
        if lm and "http" not in line.casefold():
            lesson_key = lm.group(1).replace("_", ".")
            if "." not in lesson_key and module_no:
                lesson_key = f"{module_no}.{lesson_key}"
            continue
        pm = _PASS_RE.match(line) or (
            _PASS_RE.search(line) if "http" not in line.casefold() else None
        )
        if pm and "http" not in line.casefold():
            password = pm.group(1).strip().strip(".,;)»\"'")
            continue
        pairs = extract_video_urls(line)
        if not pairs:
            lk = parse_lesson_key_from_text(line)
            if lk and not lesson_key:
                lesson_key = lk
            mod = parse_module_folder(line)
            if mod is not None and module_no is None:
                module_no = mod
            continue
        line_pwd = extract_video_password(line) or password
        for host, url in pairs:
            out.append(
                VideoLinkEntry(
                    host=host,
                    url=url,
                    password=line_pwd if host == "zoom" else "",
                    kind=kind,
                    lesson_key=lesson_key,
                    module_no=module_no,
                    description=description,
                )
            )
    return out


def link_disk_key(parent_path: str, index: int) -> str:
    return f"{parent_path.rstrip('/')}::{int(index)}"


def is_link_disk_child(stored_path: str, parent_path: str) -> bool:
    p = (parent_path or "").rstrip("/")
    s = stored_path or ""
    return s == p or s.startswith(p + "::")
