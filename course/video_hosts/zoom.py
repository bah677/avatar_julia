"""Записи Zoom Cloud Recording через yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from course.models import VideoProbe
from course.speech import SpeechSegment
from course.video_hosts.base import VideoHostAdapter
from course.video_hosts.ytdlp import YtDlpAdapter

logger = logging.getLogger(__name__)

ZOOM_ID_RE = re.compile(
    r"zoom\.us/rec/(?:share|play|clip)/([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)


def parse_zoom_id(url: str) -> str:
    m = ZOOM_ID_RE.search(url or "")
    return m.group(1) if m else ""


def normalize_zoom_url(url: str) -> str:
    """Play-ссылка из браузера часто содержит настоящий /rec/share/ в originRequestUrl."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        nested = (parse_qs(urlparse(raw).query).get("originRequestUrl") or [""])[0]
        if nested:
            nested = unquote(nested)
            if ZOOM_ID_RE.search(nested):
                raw = nested
    except Exception:
        pass
    return raw.rstrip(").,;")


def password_from_url(url: str) -> str:
    try:
        q = parse_qs(urlparse(normalize_zoom_url(url) or url or "").query)
    except Exception:
        return ""
    for key in ("pwd", "password", "passcode"):
        vals = q.get(key) or []
        if vals:
            return str(vals[0])
    return ""


class ZoomAdapter(VideoHostAdapter):
    host = "zoom"

    def __init__(self, video_password: str = "") -> None:
        self._password = (video_password or "").strip()
        self._ytdlp = YtDlpAdapter("zoom")

    async def probe(self, url: str) -> Optional[VideoProbe]:
        url = normalize_zoom_url(url) or url
        pwd = self._password or password_from_url(url)
        extra = ["--video-password", pwd] if pwd else []
        from course.video_hosts.ytdlp import _ytdlp_json, probe_from_info

        info = await _ytdlp_json([*extra, "--no-playlist", url], timeout=120.0)
        if not info:
            vid = parse_zoom_id(url)
            if not vid:
                return None
            return VideoProbe(host="zoom", video_id=vid, url=url, title="Zoom запись")
        probe = probe_from_info(info, "zoom", url)
        return VideoProbe(
            host="zoom",
            video_id=parse_zoom_id(url) or probe.video_id,
            url=url,
            title=probe.title or "Zoom запись",
            duration_sec=probe.duration_sec,
            recorded_on=probe.recorded_on,
            description=probe.description,
            chapters=probe.chapters,
            has_subtitles=probe.has_subtitles,
        )

    async def fetch_subtitles(self, url: str, probe=None) -> Optional[List[SpeechSegment]]:
        return await self._ytdlp.fetch_subtitles(url, probe)

    async def fetch_audio(self, url: str, dest_dir: str) -> Optional[str]:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        out_template = str(dest / "audio.%(ext)s")
        url = normalize_zoom_url(url) or url
        pwd = self._password or password_from_url(url)
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
        ]
        from config import config

        cookies = (getattr(config, "YTDLP_COOKIES_FILE", "") or "").strip()
        if cookies and os.path.isfile(cookies):
            cmd += ["--cookies", cookies]
        if pwd:
            cmd += ["--video-password", pwd]
        cmd.append(url)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=2400)
        except asyncio.TimeoutError:
            logger.error("zoom yt-dlp timeout: %s", url)
            return None
        except Exception as e:
            logger.warning("zoom yt-dlp: %s", e)
            return None
        if proc.returncode != 0:
            logger.warning(
                "zoom yt-dlp rc=%s %s",
                proc.returncode,
                (stderr or b"").decode("utf-8", errors="replace")[:400],
            )
            return None
        for name in sorted(os.listdir(dest)):
            path = dest / name
            if path.is_file() and path.stat().st_size > 1000:
                return str(path)
        return None

    def timecode_url(self, url: str, sec: float, *, video_id: str = "") -> str:
        s = max(0, int(sec))
        sep = "&" if "?" in (url or "") else "?"
        return f"{url}{sep}t={s}"
