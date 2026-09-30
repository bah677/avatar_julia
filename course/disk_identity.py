"""Отпечаток файла на Диске: перечитывать только при реальной смене содержимого."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Tuple


def normalize_etag(raw: str) -> str:
    s = (raw or "").strip()
    if len(s) >= 2 and s.upper().startswith("W/"):
        s = s[2:].strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s.lower()


def _mtime_unix(modified: Any) -> Optional[int]:
    if modified is None:
        return None
    if isinstance(modified, (int, float)):
        return int(modified)
    if isinstance(modified, datetime):
        dt = modified
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return None


def file_fingerprint(*, size: int = 0, modified: Any = None, etag: str = "") -> str:
    sz = int(size or 0)
    mt = _mtime_unix(modified)
    mt_s = "" if mt is None else str(mt)
    return f"v2:{sz}:{mt_s}:{normalize_etag(etag)}"


def parse_fingerprint(stored: str) -> Tuple[Optional[int], Optional[int], str]:
    """→ (size, mtime_unix, etag). Старые записи — только etag."""
    s = (stored or "").strip()
    if s.startswith("v2:"):
        parts = s.split(":", 3)
        if len(parts) == 4:
            _, sz_s, mt_s, et = parts
            try:
                sz = int(sz_s) if sz_s != "" else None
            except ValueError:
                sz = None
            try:
                mt = int(mt_s) if mt_s != "" else None
            except ValueError:
                mt = None
            return sz, mt, normalize_etag(et)
    return None, None, normalize_etag(s)


def content_changed(
    stored: str,
    *,
    size: int = 0,
    modified: Any = None,
    etag: str = "",
) -> bool:
    """True только если сменился размер. Etag и mtime Диска сами по себе не считаются."""
    prev_sz, prev_mt, prev_et = parse_fingerprint(stored)
    new_sz = int(size or 0)
    new_mt = _mtime_unix(modified)
    new_et = normalize_etag(etag)
    if prev_sz is not None and prev_sz != new_sz:
        return True
    if prev_sz is not None and prev_sz == new_sz:
        return False
    if prev_et and new_et and prev_et == new_et:
        return False
    if prev_mt is not None and new_mt is not None and abs(prev_mt - new_mt) > 2:
        return True
    return False
