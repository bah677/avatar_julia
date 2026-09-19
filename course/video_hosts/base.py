"""Интерфейс адаптера видеохостинга."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from course.models import VideoProbe
from course.speech import SpeechSegment


class VideoHostAdapter(ABC):
    host: str = ""

    @abstractmethod
    async def probe(self, url: str) -> Optional[VideoProbe]:
        ...

    @abstractmethod
    async def fetch_subtitles(self, url: str, probe: Optional[VideoProbe] = None) -> Optional[List[SpeechSegment]]:
        ...

    @abstractmethod
    async def fetch_audio(self, url: str, dest_dir: str) -> Optional[str]:
        ...

    @abstractmethod
    def timecode_url(self, url: str, sec: float, *, video_id: str = "") -> str:
        ...
