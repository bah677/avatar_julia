"""Whisper с таймкодами по 10-минутным кускам."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import List, Optional

from course.speech import SpeechSegment
from course.subtitles import chars_per_minute
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


async def transcribe_audio_segments(
    audio_path: str,
    openai_client: OpenAIClient,
    user_id: int,
    *,
    segment_sec: int = 600,
) -> List[SpeechSegment]:
    segs = await openai_client.transcribe_segments(
        audio_path, user_id, segment_sec=segment_sec
    )
    return segs or []


def subs_are_dense(segments: List[SpeechSegment], min_chars_per_min: int) -> bool:
    if not segments:
        return False
    return chars_per_minute(segments) >= float(min_chars_per_min)


async def transcribe_source_video(
    *,
    adapter,
    url: str,
    openai_client: OpenAIClient,
    user_id: int,
    dest_dir: str,
    duration_sec: Optional[int] = None,
) -> tuple[List[SpeechSegment], str]:
    """Возвращает (segments, method: subs|whisper)."""
    from config import config

    mode = (getattr(config, "COURSE_TRANSCRIBE_MODE", "auto") or "auto").strip().lower()
    min_cpm = int(getattr(config, "COURSE_SUBS_MIN_CHARS_PER_MIN", 300) or 300)

    if mode in ("auto", "subs"):
        subs = await adapter.fetch_subtitles(url)
        if subs and (mode == "subs" or subs_are_dense(subs, min_cpm)):
            return subs, "subs"
        if mode == "subs":
            return subs or [], "subs"

    audio = await adapter.fetch_audio(url, dest_dir)
    if not audio:
        return [], ""
    try:
        segs = await transcribe_audio_segments(audio, openai_client, user_id)
        return segs, "whisper"
    finally:
        try:
            if os.path.isfile(audio):
                os.remove(audio)
        except OSError:
            pass
