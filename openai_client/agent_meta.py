"""Разбор служебного блока метаданных в ответе аватара (/new)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

# В конце ответа аватара; пользователю не показывается.
_META_PATTERN = re.compile(
    r"<!--\s*AGENT_META\s*(\{.*?\})\s*-->\s*$",
    re.DOTALL | re.IGNORECASE,
)


def split_agent_meta(reply: str) -> Tuple[str, Dict[str, Any]]:
    """
    Отделяет видимый текст от JSON в ``<!-- AGENT_META {...} -->``.

    Returns:
        (text_for_user, meta_dict) — meta может быть пустым.
    """
    raw = (reply or "").strip()
    if not raw:
        return "", {}

    m = _META_PATTERN.search(raw)
    if not m:
        return raw, {}

    visible = raw[: m.start()].rstrip()
    try:
        meta = json.loads(m.group(1))
        if not isinstance(meta, dict):
            meta = {}
    except json.JSONDecodeError:
        meta = {}

    out: Dict[str, Any] = {}
    for key in ("format", "lesson_key", "task_summary", "product", "content_type"):
        v = meta.get(key)
        if v is not None and str(v).strip():
            out[key] = str(v).strip()[:500]
    if "is_draft" in meta:
        val = meta.get("is_draft")
        out["is_draft"] = bool(val) if not isinstance(val, str) else val.strip().lower() in ("1", "true", "yes")

    return visible, out
