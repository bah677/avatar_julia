"""
Скачивание и транскрипция аудиодорожки YouTube-видео через yt-dlp + Whisper.

Используется в RAG-индексере: если в сообщении обнаружена YouTube-ссылка,
аудио скачивается, транскрибируется через Whisper и текст идёт в RAG.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
from typing import Optional

from bot.media_processing.config.settings import MEDIA_LIMITS
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)

_YT_RE = re.compile(
    r"(?:https?://)?(?:"
    r"(?:www\.)?youtube\.com/watch\?[^\s]*v=[\w-]+"
    r"|(?:www\.)?youtube\.com/shorts/[\w-]+"
    r"|(?:www\.)?youtube\.com/live/[\w-]+"
    r"|youtu\.be/[\w-]+"
    r")",
    re.IGNORECASE,
)

_WHISPER_MAX_FILE_BYTES = 24 * 1024 * 1024  # 24 MB — запас под лимит Whisper API (25 MB)


def extract_youtube_urls(text: str) -> list[str]:
    """Все YouTube-ссылки из текста."""
    return _YT_RE.findall(text or "")


async def download_youtube_audio(url: str, *, max_duration_sec: int = 0) -> Optional[str]:
    """Скачивает аудиодорожку YouTube-видео в mp3.

    Returns:
        Путь к файлу или None при ошибке.
    """
    tmp_dir = tempfile.mkdtemp(prefix="yt_rag_")
    out_template = os.path.join(tmp_dir, "audio.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",  # средний битрейт (~128 kbps) — баланс качества и размера
        "-o", out_template,
        "--no-warnings",
        "--quiet",
    ]
    if max_duration_sec > 0:
        cmd += ["--match-filter", f"duration<={max_duration_sec}"]
    cmd.append(url)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[:500]
            logger.error("yt-dlp failed (rc=%s): %s", proc.returncode, err)
            return None

        for fname in os.listdir(tmp_dir):
            fpath = os.path.join(tmp_dir, fname)
            if os.path.isfile(fpath):
                return fpath

        logger.error("yt-dlp: файл не найден в %s", tmp_dir)
        return None

    except asyncio.TimeoutError:
        logger.error("yt-dlp: timeout (600s) для %s", url)
        return None
    except Exception as e:
        logger.error("yt-dlp exception: %s", e, exc_info=True)
        return None


async def transcribe_youtube_audio(
    audio_path: str,
    openai_client: OpenAIClient,
    user_id: int,
) -> Optional[str]:
    """Транскрибирует скачанный аудиофайл YouTube.

    Если файл > 24 MB, разбивает на сегменты через ffmpeg и транскрибирует каждый.
    """
    file_size = os.path.getsize(audio_path)

    if file_size <= _WHISPER_MAX_FILE_BYTES:
        return await openai_client.transcribe_voice(
            audio_file_path=audio_path,
            user_id=user_id,
        )

    logger.info(
        "YouTube audio %s MB > %s MB — разбиваем на сегменты",
        file_size // (1024 * 1024),
        _WHISPER_MAX_FILE_BYTES // (1024 * 1024),
    )
    return await _transcribe_in_segments(audio_path, openai_client, user_id)


async def _transcribe_in_segments(
    audio_path: str,
    openai_client: OpenAIClient,
    user_id: int,
    segment_sec: int = 600,
) -> Optional[str]:
    """Разбивает аудио на сегменты и транскрибирует каждый."""
    tmp_dir = tempfile.mkdtemp(prefix="yt_seg_")
    seg_pattern = os.path.join(tmp_dir, "seg_%03d.mp3")

    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-f", "segment",
        "-segment_time", str(segment_sec),
        "-c:a", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        seg_pattern,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=300)

        if proc.returncode != 0:
            logger.error("ffmpeg segment split failed (rc=%s)", proc.returncode)
            return None

        segments = sorted(
            os.path.join(tmp_dir, f)
            for f in os.listdir(tmp_dir)
            if f.startswith("seg_") and f.endswith(".mp3")
        )
        if not segments:
            logger.error("ffmpeg: нет сегментов в %s", tmp_dir)
            return None

        logger.info("YouTube: разбито на %d сегментов по ~%d мин", len(segments), segment_sec // 60)

        parts: list[str] = []
        for seg in segments:
            text = await openai_client.transcribe_voice(
                audio_file_path=seg,
                user_id=user_id,
            )
            if text and text.strip():
                parts.append(text.strip())

        return "\n".join(parts) if parts else None

    except asyncio.TimeoutError:
        logger.error("ffmpeg segment: timeout")
        return None
    except Exception as e:
        logger.error("ffmpeg segment error: %s", e, exc_info=True)
        return None
    finally:
        for f in os.listdir(tmp_dir):
            try:
                os.unlink(os.path.join(tmp_dir, f))
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
