"""Каталоги data/course/<source_id>/ и временные файлы."""

from __future__ import annotations

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


def tmp_dir() -> Path:
    from config import config

    raw = (getattr(config, "COURSE_TMP_DIR", "") or "data/tmp").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = project_root() / p
    p.mkdir(parents=True, exist_ok=True)
    return p
