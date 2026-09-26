"""Vimeo: Data API владельца, иначе yt-dlp с cookies."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

import httpx

from course.models import VideoProbe
from course.speech import SpeechSegment
from course.subtitles import parse_vtt
from course.video_hosts.base import VideoHostAdapter
from course.video_hosts.disk_media import extract_audio_from_video_file
from course.video_hosts.vimeo_api import (
    auth_header,
    parse_recorded_on,
    parse_vimeo_ref,
    pick_audio_source,
    pick_texttrack,
    video_api_path,
    vimeo_get,
)
from course.video_hosts.ytdlp import YtDlpAdapter

logger = logging.getLogger(__name__)

_VIDEO_FIELDS = (
    "name,description,duration,created_time,release_time,privacy,"
    "link,player_embed_url,files,download,play,status,is_playable"
)


class VimeoAdapter(VideoHostAdapter):
    host = "vimeo"

    def __init__(self):
        self._ytdlp = YtDlpAdapter("vimeo")

    async def probe(self, url: str) -> Optional[VideoProbe]:
        vid, hashed = parse_vimeo_ref(url)
        if not vid:
            return await self._ytdlp.probe(url)
        try:
            data = await vimeo_get(
                video_api_path(vid, hashed),
                params={"fields": _VIDEO_FIELDS},
            )
        except Exception as e:
            logger.warning("Vimeo API probe failed: %s", e)
            return await self._ytdlp.probe(url)
        duration = data.get("duration")
        try:
            duration_sec = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_sec = None
        return VideoProbe(
            host="vimeo",
            video_id=vid,
            url=str(data.get("link") or url),
            title=str(data.get("name") or ""),
            duration_sec=duration_sec,
            recorded_on=parse_recorded_on(
                str(data.get("release_time") or data.get("created_time") or "")
            ),
            description=str(data.get("description") or "")[:4000],
        )

    async def fetch_subtitles(
        self, url: str, probe: Optional[VideoProbe] = None
    ) -> Optional[List[SpeechSegment]]:
        vid, hashed = parse_vimeo_ref(url)
        if vid:
            try:
                payload = await vimeo_get(
                    video_api_path(vid, hashed) + "/texttracks",
                )
                tracks = payload.get("data") if isinstance(payload.get("data"), list) else []
                track = pick_texttrack([t for t in tracks if isinstance(t, dict)])
                link = str((track or {}).get("link") or "")
                if link:
                    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                        r = await client.get(link)
                        r.raise_for_status()
                        segs = parse_vtt(r.text)
                    if segs:
                        return segs
            except Exception as e:
                logger.warning("Vimeo texttracks failed: %s", e)
        return await self._ytdlp.fetch_subtitles(url, probe)

    async def fetch_audio(self, url: str, dest_dir: str) -> Optional[str]:
        vid, hashed = parse_vimeo_ref(url)
        if vid:
            path = await self._download_via_api(vid, hashed, dest_dir)
            if path:
                return path
            token = ""
            try:
                from config import config

                token = (getattr(config, "VIMEO_ACCESS_TOKEN", "") or "").strip()
            except Exception:
                token = ""
            if not token:
                logger.error(
                    "Vimeo %s: нет файлов без токена владельца. "
                    "Добавьте VIMEO_ACCESS_TOKEN со scope public,private,video_files",
                    vid,
                )
        return await self._ytdlp.fetch_audio(url, dest_dir)

    async def _download_via_api(self, vid: str, hashed: str, dest_dir: str) -> Optional[str]:
        try:
            data = await vimeo_get(
                video_api_path(vid, hashed),
                params={"fields": _VIDEO_FIELDS},
            )
        except Exception as e:
            logger.warning("Vimeo API files: %s", e)
            return None
        src = pick_audio_source(data)
        if not src:
            return None
        link = str(src.get("link") or "")
        if not link:
            return None
        from config import config

        max_gb = float(getattr(config, "COURSE_MEDIA_MAX_GB", 4) or 4)
        size = 0
        try:
            size = int(src.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > max_gb * 1024 * 1024 * 1024:
            logger.warning("Vimeo file too large: %s bytes", size)
            return None
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        ext = ".mp3" if str(src.get("type") or "").startswith("audio/") else ".mp4"
        local = dest / f"vimeo{ext}"
        headers = {
            "Authorization": await auth_header(),
            "User-Agent": "avatar-course/1.0",
        }
        try:
            async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
                async with client.stream("GET", link, headers=headers) as r:
                    r.raise_for_status()
                    with open(local, "wb") as fh:
                        async for chunk in r.aiter_bytes():
                            fh.write(chunk)
        except Exception as e:
            logger.warning("Vimeo download failed: %s", e)
            return None
        if not local.is_file() or local.stat().st_size < 1000:
            return None
        if ext == ".mp3":
            return str(local)
        out_mp3 = dest / "audio.mp3"
        ok = await extract_audio_from_video_file(local, out_mp3)
        try:
            local.unlink(missing_ok=True)
        except OSError:
            pass
        return str(out_mp3) if ok else None

    def timecode_url(self, url: str, sec: float, *, video_id: str = "") -> str:
        return self._ytdlp.timecode_url(url, sec, video_id=video_id)
