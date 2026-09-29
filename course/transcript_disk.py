"""Сырая расшифровка урока → файл на Яндекс.Диске."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

from course.speech import SpeechSegment

logger = logging.getLogger(__name__)

TRANSCRIPT_FILENAME = "_Расшифровка.txt"


def _mmss(sec: float) -> str:
    s = max(0, int(sec))
    h, rem = divmod(s, 3600)
    m, sec_i = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{sec_i:02d}"
    return f"{m:02d}:{sec_i:02d}"


def format_raw_transcript(
    *,
    lesson_key: str = "",
    title: str = "",
    url: str = "",
    method: str = "",
    segments: Sequence[SpeechSegment],
) -> str:
    head: list[str] = []
    label = " ".join(x for x in (f"Урок {lesson_key}".strip(), title) if x).strip()
    if label:
        head.append(label)
    if url:
        head.append(f"Источник: {url}")
    if method:
        head.append(f"Расшифровка: {method}")
    body: list[str] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        body.append(f"[{_mmss(seg.start_sec)}] {text}")
    parts = ["\n".join(head).strip(), "\n".join(body).strip()]
    return "\n\n".join(p for p in parts if p) + "\n"


def _parent(path: str) -> str:
    p = (path or "").rstrip("/")
    if "/" not in p:
        return ""
    return p.rsplit("/", 1)[0]


async def resolve_lesson_folder(
    *,
    storage,
    lesson: Optional[Dict[str, Any]],
    src: Dict[str, Any],
) -> str:
    folder = (lesson or {}).get("disk_path") or ""
    if folder:
        return folder.rstrip("/")
    if src.get("origin") == "disk" and src.get("disk_path"):
        return _parent(str(src["disk_path"]))
    lesson_id = (lesson or {}).get("id") or src.get("lesson_id")
    if lesson_id:
        rows = await storage.list_course_sources_for_lesson(int(lesson_id))
        for row in rows:
            if row.get("origin") == "disk" and row.get("disk_path"):
                return _parent(str(row["disk_path"]))
    return await _guess_or_create_folder(lesson, src)


async def _guess_or_create_folder(
    lesson: Optional[Dict[str, Any]],
    src: Dict[str, Any],
) -> str:
    from config import config
    from course.products import get_registry
    from yandex_disk.webdav import YandexDiskWebDAV

    root = (config.COURSE_DISK_ROOT or "/Аватар").rstrip("/")
    product = get_registry().by_id(src.get("product_id") or "")
    product_folder = (product.disk_folder if product else "") or (src.get("product_id") or "mbt")
    course_root = f"{root}/{product_folder}/Курс"
    module_no = (lesson or {}).get("module_no")
    key = (lesson or {}).get("lesson_key") or ""
    title = (lesson or {}).get("title") or src.get("title") or ""
    dav = YandexDiskWebDAV(config.YANDEX_DISK_LOGIN, config.YANDEX_DISK_PASSWORD)
    module_dir = course_root
    if module_no:
        module_dir = f"{course_root}/Модуль {int(module_no)}"
        try:
            for href in await dav.list_dirs(course_root):
                name = href.rsplit("/", 1)[-1]
                if name.lower().startswith(f"модуль {int(module_no)}") or name.lower().startswith(
                    f"модуль {int(module_no)}."
                ):
                    module_dir = href
                    break
        except Exception as e:
            logger.warning("list module dirs %s: %s", course_root, e)
    lesson_name = f"Урок {key}"
    if title and not title.lower().startswith("урок "):
        lesson_name = f"Урок {key}. {title}"
    return f"{module_dir}/{lesson_name}"


async def upload_raw_transcript(
    *,
    storage,
    src: Dict[str, Any],
    segments: Sequence[SpeechSegment],
    method: str,
) -> str:
    from config import config
    from yandex_disk.webdav import YandexDiskWebDAV

    lesson = None
    if src.get("lesson_id"):
        lesson = await storage.get_course_lesson_by_id(src["lesson_id"])
    folder = await resolve_lesson_folder(storage=storage, lesson=lesson, src=src)
    if not folder:
        raise RuntimeError("нет папки урока на Диске")
    text = format_raw_transcript(
        lesson_key=(lesson or {}).get("lesson_key") or "",
        title=(lesson or {}).get("title") or src.get("title") or "",
        url=src.get("url") or "",
        method=method,
        segments=segments,
    )
    dav = YandexDiskWebDAV(config.YANDEX_DISK_LOGIN, config.YANDEX_DISK_PASSWORD)
    await dav.mkdir_p(folder)
    remote = f"{folder.rstrip('/')}/{TRANSCRIPT_FILENAME}"
    await dav.put_text(remote, text)
    if lesson and not (lesson.get("disk_path") or "") and src.get("lesson_id"):
        try:
            await storage.upsert_course_lesson(
                product_id=src.get("product_id") or "",
                lesson_key=(lesson.get("lesson_key") or ""),
                lesson_no=int(lesson.get("lesson_no") or 0),
                module_no=lesson.get("module_no"),
                title=lesson.get("title") or "",
                disk_path=folder,
            )
        except Exception as e:
            logger.warning("save lesson disk_path: %s", e)
    logger.info("transcript uploaded %s (%s chars)", remote, len(text))
    return remote
