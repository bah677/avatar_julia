"""Видео/аудио с Яндекс.Диска: скачать, звук, длительность."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from course.models import VideoProbe
from course.paths import tmp_dir
from course.speech import SpeechSegment
from course.video_hosts.base import VideoHostAdapter
from yandex_disk.webdav import YandexDiskWebDAV

logger = logging.getLogger(__name__)


def probe_media_duration_sec(path: str | Path) -> Optional[float]:
    p = Path(path)
    if not p.is_file():
        return None
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        proc = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip())
    except Exception:
        return None


async def extract_audio_from_video_file(video_local: Path, out_mp3: Path) -> bool:
    if not shutil.which("ffmpeg"):
        logger.error("ffmpeg not found")
        return False

    def _extract() -> bool:
        cmd = [
            "ffmpeg", "-y", "-i", str(video_local),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "32k",
            str(out_mp3),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        return proc.returncode == 0 and out_mp3.is_file() and out_mp3.stat().st_size > 1000

    return await asyncio.to_thread(_extract)


class DiskMediaAdapter(VideoHostAdapter):
    host = "disk"

    def __init__(self, webdav: Optional[YandexDiskWebDAV] = None):
        self._webdav = webdav

    def _client(self) -> YandexDiskWebDAV:
        if self._webdav:
            return self._webdav
        from config import config

        self._webdav = YandexDiskWebDAV(
            config.YANDEX_DISK_LOGIN, config.YANDEX_DISK_PASSWORD
        )
        return self._webdav

    async def probe(self, url: str) -> Optional[VideoProbe]:
        return VideoProbe(host="disk", video_id="", url=url, title=Path(url).name)

    async def fetch_subtitles(self, url: str, probe=None) -> Optional[list[SpeechSegment]]:
        return None

    async def fetch_audio(self, url: str, dest_dir: str) -> Optional[str]:
        from config import config

        max_gb = float(getattr(config, "COURSE_MEDIA_MAX_GB", 4) or 4)
        client = self._client()
        meta = await client.get_file_meta(url)
        if meta and meta.size > max_gb * 1024 * 1024 * 1024:
            logger.warning("disk media too large: %s bytes", meta.size)
            return None
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        ext = Path(url).suffix.lower() or ".bin"
        local = dest / f"src{ext}"
        await client.download(url, str(local))
        if ext in {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac"}:
            return str(local)
        out_mp3 = dest / "audio.mp3"
        ok = await extract_audio_from_video_file(local, out_mp3)
        try:
            if local != out_mp3:
                local.unlink(missing_ok=True)
        except OSError:
            pass
        return str(out_mp3) if ok else None

    def timecode_url(self, url: str, sec: float, *, video_id: str = "") -> str:
        return ""
