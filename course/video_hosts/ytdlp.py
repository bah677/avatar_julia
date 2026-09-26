"""YouTube и Vimeo через yt-dlp."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from course.models import VideoProbe
from course.speech import SpeechSegment
from course.subtitles import parse_json3, parse_vtt
from course.video_hosts.base import VideoHostAdapter

logger = logging.getLogger(__name__)

YT_RE = re.compile(
    r"(?:https?://)?(?:"
    r"(?:www\.)?youtube\.com/(?:watch\?[^\s]*v=|shorts/|live/|embed/)[\w-]+"
    r"|youtu\.be/[\w-]+"
    r"|youtube\.com/playlist\?[^\s]*list=[\w-]+"
    r")",
    re.IGNORECASE,
)
VIMEO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?vimeo\.com/(?:channels/[\w]+/)?(\d+)(?:/[0-9a-f]+)?",
    re.IGNORECASE,
)


def extract_youtube_urls(text: str) -> list[str]:
    from bot.media_processing.processors.youtube import extract_youtube_urls as _base

    found = _base(text or "")
    extra = re.findall(
        r"https?://(?:www\.)?youtube\.com/playlist\?[^\s]*list=[\w-]+",
        text or "",
        re.IGNORECASE,
    )
    out = list(found)
    for u in extra:
        if u not in out:
            out.append(u)
    return out


def extract_vimeo_urls(text: str) -> list[str]:
    out = []
    for m in re.finditer(
        r"https?://(?:www\.)?vimeo\.com/\S+",
        text or "",
        re.IGNORECASE,
    ):
        out.append(m.group(0).rstrip(").,;"))
    return out


def extract_kinescope_urls(text: str) -> list[str]:
    return [
        m.group(0).rstrip(").,;")
        for m in re.finditer(
            r"https?://(?:kinescope\.io|player\.kinescope\.io)/\S+",
            text or "",
            re.IGNORECASE,
        )
    ]


def extract_video_urls(text: str) -> list[tuple[str, str]]:
    """[(host, url), ...] с сохранением порядка."""
    seen = set()
    out: list[tuple[str, str]] = []
    for url in extract_youtube_urls(text):
        if url not in seen:
            seen.add(url)
            out.append(("youtube", url))
    for url in extract_vimeo_urls(text):
        if url not in seen:
            seen.add(url)
            out.append(("vimeo", url))
    for url in extract_kinescope_urls(text):
        if url not in seen:
            seen.add(url)
            out.append(("kinescope", url))
    return out


def detect_host(url: str) -> str:
    u = (url or "").lower()
    if "youtu" in u:
        return "youtube"
    if "vimeo" in u:
        return "vimeo"
    if "kinescope" in u:
        return "kinescope"
    return ""


async def _ytdlp_json(args: List[str], *, timeout: float = 90.0) -> Optional[Dict[str, Any]]:
    from config import config

    cmd = [sys.executable, "-m", "yt_dlp", "-J", "--skip-download", "--no-warnings"]
    cookies = (getattr(config, "YTDLP_COOKIES_FILE", "") or "").strip()
    if cookies and os.path.isfile(cookies):
        cmd += ["--cookies", cookies]
        if any("vimeo" in str(a).lower() for a in args):
            cmd += ["--extractor-args", "vimeo:client=web"]
    cmd += args
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            logger.warning("yt-dlp -J rc=%s %s", proc.returncode, (stderr or b"")[:400])
            return None
        return json.loads(stdout.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("yt-dlp -J failed: %s", e)
        return None


def _parse_upload_date(raw: str) -> Optional[date]:
    s = (raw or "").strip()
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return datetime.strptime(s[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def probe_from_info(info: Dict[str, Any], host: str, url: str) -> VideoProbe:
    vid = str(info.get("id") or "")
    duration = info.get("duration")
    try:
        duration_sec = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_sec = None
    subs = bool(info.get("subtitles") or info.get("automatic_captions"))
    chapters = []
    for ch in info.get("chapters") or []:
        if isinstance(ch, dict):
            chapters.append(
                {
                    "start": ch.get("start_time"),
                    "title": ch.get("title") or "",
                }
            )
    return VideoProbe(
        host=host,
        video_id=vid,
        url=url,
        title=str(info.get("title") or ""),
        duration_sec=duration_sec,
        recorded_on=_parse_upload_date(str(info.get("upload_date") or "")),
        description=str(info.get("description") or "")[:4000],
        chapters=chapters,
        has_subtitles=subs,
    )


class YtDlpAdapter(VideoHostAdapter):
    def __init__(self, host: str = "youtube"):
        self.host = host

    async def probe(self, url: str) -> Optional[VideoProbe]:
        info = await _ytdlp_json(["--no-playlist", url])
        if not info:
            return None
        if info.get("_type") == "playlist":
            return None
        return probe_from_info(info, self.host, url)

    async def probe_playlist(self, url: str, *, limit: int = 100) -> List[VideoProbe]:
        info = await _ytdlp_json(
            ["--flat-playlist", "--playlist-end", str(int(limit)), url],
            timeout=180.0,
        )
        if not info:
            return []
        entries = info.get("entries") or []
        out: List[VideoProbe] = []
        for i, ent in enumerate(entries[:limit]):
            if not isinstance(ent, dict):
                continue
            vid = str(ent.get("id") or "")
            webpage = str(ent.get("url") or ent.get("webpage_url") or "")
            if self.host == "youtube" and vid and not webpage:
                webpage = f"https://www.youtube.com/watch?v={vid}"
            out.append(
                VideoProbe(
                    host=self.host,
                    video_id=vid,
                    url=webpage or url,
                    title=str(ent.get("title") or ""),
                    duration_sec=int(ent["duration"]) if ent.get("duration") else None,
                    playlist_index=i + 1,
                )
            )
        return out

    async def fetch_subtitles(
        self, url: str, probe: Optional[VideoProbe] = None
    ) -> Optional[List[SpeechSegment]]:
        tmp = tempfile.mkdtemp(prefix="subs_")
        from config import config

        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "ru,ru-orig,ru.*",
            "--sub-format", "json3/vtt/best",
            "-o", os.path.join(tmp, "v.%(ext)s"),
            "--no-warnings",
            "--quiet",
        ]
        cookies = (getattr(config, "YTDLP_COOKIES_FILE", "") or "").strip()
        if cookies and os.path.isfile(cookies):
            cmd += ["--cookies", cookies]
            if "vimeo" in (url or "").lower():
                cmd += ["--extractor-args", "vimeo:client=web"]
        cmd.append(url)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=120)
        except Exception as e:
            logger.warning("yt-dlp subs failed: %s", e)
            return None
        files = []
        for fn in os.listdir(tmp):
            files.append(os.path.join(tmp, fn))
        # ручные субтитры предпочтительнее авто
        manuals = [f for f in files if ".ru." in f.lower() and "auto" not in f.lower()]
        autos = [f for f in files if f not in manuals]
        ordered = manuals + autos
        for path in ordered:
            try:
                raw = open(path, "r", encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if path.endswith(".json3") or '"tStartMs"' in raw[:500]:
                segs = parse_json3(raw)
            else:
                segs = parse_vtt(raw)
            if segs:
                return segs
        return None

    async def fetch_audio(self, url: str, dest_dir: str) -> Optional[str]:
        from bot.media_processing.processors.youtube import download_youtube_audio

        return await download_youtube_audio(url)

    def timecode_url(self, url: str, sec: float, *, video_id: str = "") -> str:
        s = max(0, int(sec))
        if self.host == "vimeo":
            vid = video_id or (re.search(r"vimeo\.com/(\d+)", url or "") or type("m", (), {"group": lambda *_: ""})()).group(1)
            if vid:
                return f"https://vimeo.com/{vid}#t={s}s"
            return f"{url}#t={s}s"
        vid = video_id
        if not vid:
            m = re.search(r"(?:v=|/shorts/|/live/|youtu\.be/)([\w-]{6,})", url or "")
            vid = m.group(1) if m else ""
        if vid:
            return f"https://youtu.be/{vid}?t={s}"
        return url or ""
