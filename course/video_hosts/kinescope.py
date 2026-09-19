"""Kinescope — этап 5: заглушка."""

from __future__ import annotations

import logging
from typing import List, Optional

from course.models import VideoProbe
from course.speech import SpeechSegment
from course.video_hosts.base import VideoHostAdapter

logger = logging.getLogger(__name__)


class KinescopeAdapter(VideoHostAdapter):
    host = "kinescope"

    async def probe(self, url: str) -> Optional[VideoProbe]:
        logger.warning("Kinescope: этап 5, API не подключён (%s)", url)
        return None

    async def fetch_subtitles(self, url: str, probe=None) -> Optional[List[SpeechSegment]]:
        return None

    async def fetch_audio(self, url: str, dest_dir: str) -> Optional[str]:
        return None

    def timecode_url(self, url: str, sec: float, *, video_id: str = "") -> str:
        s = max(0, int(sec))
        return f"{url}#t={s}"
