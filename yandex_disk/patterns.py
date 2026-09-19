"""Сопоставление имён файлов с масками (*.mp3, 2026-_____.mp3, re:…)."""

from __future__ import annotations

import fnmatch
import re
from typing import Iterable, List


def mask_to_fnmatch(pattern: str) -> str:
    """
    Glob с расширением: ``_`` в маске = один любой символ (как ``?``).

    ``*.mp3``, ``2026-_____.mp3`` → ``2026-?????.mp3``.
    """
    p = (pattern or "").strip()
    if not p or p.startswith("re:"):
        return p
    return "".join("?" if ch == "_" else ch for ch in p)


def file_matches_masks(filename: str, masks: Iterable[str]) -> bool:
    name = (filename or "").strip()
    if not name:
        return False
    items: List[str] = [str(m).strip() for m in (masks or []) if str(m).strip()]
    if not items:
        return True
    for raw in items:
        if raw.startswith("re:"):
            try:
                if re.search(raw[3:], name, re.IGNORECASE):
                    return True
            except re.error:
                continue
            continue
        fn = mask_to_fnmatch(raw)
        if fnmatch.fnmatchcase(name.lower(), fn.lower()):
            return True
    return False
