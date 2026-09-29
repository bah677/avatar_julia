"""Каталоги data/course/<source_id>/ и временные файлы."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def course_data_dir() -> Path:
    p = project_root() / "data" / "course"
    p.mkdir(parents=True, exist_ok=True)
    return p


def source_dir(source_id: UUID | str) -> Path:
    p = course_data_dir() / str(source_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def extracted_plain_text(dest: Path) -> str:
    """Текст из pages.json или transcript.json источника."""
    pages = dest / "pages.json"
    if pages.is_file():
        data = json.loads(pages.read_text(encoding="utf-8"))
        text = "\n\n".join((p.get("text") or "") for p in data).strip()
        if text:
            return text
    tr = dest / "transcript.json"
    if tr.is_file():
        data = json.loads(tr.read_text(encoding="utf-8"))
        parts = []
        for seg in data.get("segments") or []:
            t = (seg.get("text") or "").strip()
            if t:
                parts.append(t)
        return "\n".join(parts).strip()
    return ""


def tmp_dir() -> Path:
    from config import config

    raw = (getattr(config, "COURSE_TMP_DIR", "") or "data/tmp").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = project_root() / p
    p.mkdir(parents=True, exist_ok=True)
    return p
