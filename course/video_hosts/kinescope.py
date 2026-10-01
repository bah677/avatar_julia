"""Kinescope: публичные ссылки плеера через yt-dlp (аудио → Whisper)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from course.models import VideoProbe
from course.speech import SpeechSegment
from course.video_hosts.base import VideoHostAdapter
from course.video_hosts.ytdlp import YtDlpAdapter

logger = logging.getLogger(__name__)

_ID_RE = re.compile(
    r"(?:kinescope\.io|player\.kinescope\.io)/(?:embed/)?([A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)


def parse_kinescope_id(url: str) -> str:
    m = _ID_RE.search(url or "")
    if not m:
        return ""
    vid = m.group(1)
    if vid.lower() in {"embed", "oembed", "api", "player"}:
        return ""
    return vid


class KinescopeAdapter(VideoHostAdapter):
    host = "kinescope"

    def __init__(self) -> None:
        self._ytdlp = YtDlpAdapter("kinescope")

    async def probe(self, url: str) -> Optional[VideoProbe]:
        return await self._ytdlp.probe(url)

    async def fetch_subtitles(self, url: str, probe=None) -> Optional[List[SpeechSegment]]:
        return await self._ytdlp.fetch_subtitles(url, probe)

    async def fetch_audio(self, url: str, dest_dir: str) -> Optional[str]:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        out_template = str(dest / "audio.%(ext)s")
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "-f",
            "ba/bestaudio/worst",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "5",
            "-o",
            out_template,
            "--no-warnings",
            "--quiet",
            url,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=2400)
        except asyncio.TimeoutError:
            logger.error("kinescope yt-dlp timeout: %s", url)
            return None
        except Exception as e:
            logger.warning("kinescope yt-dlp: %s", e)
            return None
        if proc.returncode != 0:
            logger.warning(
                "kinescope yt-dlp rc=%s %s",
                proc.returncode,
                (stderr or b"").decode("utf-8", errors="replace")[:400],
            )
            return None
        for name in sorted(os.listdir(dest)):
            path = dest / name
            if path.is_file() and path.stat().st_size > 1000:
                return str(path)
        logger.warning("kinescope: нет аудиофайла в %s", dest)
        return None

    def timecode_url(self, url: str, sec: float, *, video_id: str = "") -> str:
        s = max(0, int(sec))
        vid = video_id or parse_kinescope_id(url)
        if vid:
            return f"https://kinescope.io/{vid}?seek={s}"
        sep = "&" if "?" in (url or "") else "?"
        return f"{url}{sep}seek={s}"
