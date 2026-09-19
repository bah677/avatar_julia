"""Сохранение видео-вложений из писем Телемоста."""

from __future__ import annotations

import logging
import re
from email.message import Message
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_VIDEO_EXT = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})


def _safe_name(name: str) -> str:
    base = (name or "recording").strip()
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)
    return base[:120] or "recording.mp4"


def extract_video_bytes(msg: Message) -> Tuple[bytes, str]:
    """(payload, filename) или пустые bytes."""
    best: Tuple[int, bytes, str] = (0, b"", "")
    if not msg.is_multipart():
        return b"", ""
    for part in msg.walk():
        fname = part.get_filename() or ""
        ext = Path(fname).suffix.lower()
        ctype = (part.get_content_type() or "").lower()
        if ext not in _VIDEO_EXT and not ctype.startswith("video/"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        size = len(payload)
        if size > best[0]:
            best = (size, payload, fname or f"telemost{ext or '.mp4'}")
    return best[1], best[2]


def save_video_attachment(
    payload: bytes,
    filename: str,
    *,
    imap_uid: str,
    base_dir: str | Path,
) -> Optional[str]:
    if not payload:
        return None
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    ext = Path(_safe_name(filename)).suffix.lower() or ".mp4"
    if ext not in _VIDEO_EXT:
        ext = ".mp4"
    out = root / f"{imap_uid}{ext}"
    try:
        out.write_bytes(payload)
        logger.info("telemost video saved: %s (%s bytes)", out, len(payload))
        return str(out.resolve())
    except Exception as e:
        logger.error("save_video_attachment %s: %s", out, e)
        return None
