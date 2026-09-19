"""Скачивание записи эфира по ссылке из письма Телемоста."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def _safe_meeting_id(meeting_id: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (meeting_id or "unknown").strip())[:64]


def _yt_dlp_bin() -> Optional[str]:
    return shutil.which("yt-dlp") or shutil.which("youtube-dl")


def _download_via_yt_dlp(url: str, out_path: Path) -> bool:
    bin_name = _yt_dlp_bin()
    if not bin_name:
        return False
    out_tpl = str(out_path.with_suffix(".%(ext)s"))
    cmd = [
        bin_name,
        "-f",
        "best[ext=mp4]/best",
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-o",
        out_tpl,
        url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "yt-dlp failed: %s",
                (proc.stderr or proc.stdout or "")[-600:],
            )
            return False
        if out_path.is_file():
            return True
        candidates = sorted(out_path.parent.glob(out_path.stem + ".*"))
        for c in candidates:
            if c.suffix.lower() in {".mp4", ".mkv", ".webm"} and c.stat().st_size > 0:
                c.rename(out_path)
                return True
        return False
    except Exception as e:
        logger.error("yt-dlp download: %s", e)
        return False


def _download_direct(url: str, out_path: Path) -> bool:
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=600.0) as r:
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            if "text/html" in ctype and "video" not in ctype:
                return False
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("wb") as f:
                for chunk in r.iter_bytes(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        return out_path.is_file() and out_path.stat().st_size > 10_000
    except Exception as e:
        logger.warning("direct download %s: %s", url[:80], e)
        return False


def download_recording_video(
    url: str,
    meeting_id: str,
    *,
    dest_dir: str | Path,
) -> Optional[str]:
    """
    Скачивает видео в ``dest_dir/{meeting_id}.mp4``.
    Сначала yt-dlp (Я.Диск / Телемост), затем прямой HTTP.
    """
    link = (url or "").strip()
    if not link:
        return None
    root = Path(dest_dir)
    root.mkdir(parents=True, exist_ok=True)
    mid = _safe_meeting_id(meeting_id) or "recording"
    out = root / f"{mid}.mp4"
    if out.is_file() and out.stat().st_size > 10_000:
        return str(out.resolve())

    host = (urlparse(link).netloc or "").lower()
    if _yt_dlp_bin() and (
        "yandex" in host
        or "yadi.sk" in host
        or "telemost" in host
        or "disk" in host
    ):
        if _download_via_yt_dlp(link, out):
            logger.info("recording downloaded via yt-dlp: %s", out)
            return str(out.resolve())

    if _download_direct(link, out):
        logger.info("recording downloaded direct: %s", out)
        return str(out.resolve())

    if _yt_dlp_bin() and _download_via_yt_dlp(link, out):
        return str(out.resolve())

    logger.error("recording download failed meeting_id=%s url=%s", meeting_id, link[:120])
    return None
