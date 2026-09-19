"""
Клиент для работы с OpenAI API: только Whisper (распознавание речи) и Vision
(описание изображений). Здесь же — централизованное логирование расхода токенов
в БД (`token_usage` + `interaction_logs`).

Историю Assistants API убрали: основной диалоговый путь идёт через
`openai_client.agents_client.AgentsClient` (DeepSeek), а медиа-обработка в
`bot/media_processing/*` использует только `transcribe_voice` и `describe_image`.
"""

import asyncio
import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from config import config
from storage.db.llm_token_normalize import extract_token_counts_and_extras
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

_WHISPER_MAX_FILE_BYTES = 24 * 1024 * 1024


async def _compress_audio_for_whisper(src: str) -> Optional[str]:
    """Сжимает аудио до mono 16kHz mp3, чтобы уложиться в лимит Whisper (~25MB)."""
    fd, dst = tempfile.mkstemp(suffix=".mp3", prefix="whisper_")
    os.close(fd)
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "32k", dst,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) <= 0:
            logger.warning("ffmpeg compress for Whisper failed rc=%s", proc.returncode)
            os.remove(dst)
            return None
        return dst
    except Exception as e:
        logger.warning("ffmpeg compress for Whisper failed: %s", e)
        try:
            os.remove(dst)
        except OSError:
            pass
        return None


class OpenAIClient:
    """Тонкая обёртка над AsyncOpenAI для распознавания голоса и фото."""

    def __init__(self, user_storage: UserStorage):
        self.user_storage = user_storage
        self.client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

    # =====================================================
    # PRIVATE: учёт токенов
    # =====================================================

    async def _log_llm_metrics(
        self,
        user_id: int,
        provider: str,
        model: str,
        *,
        usage: Any = None,
        request_kind: str,
        thread_id: Optional[str] = None,
        duration_sec: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Запись в ``token_usage`` (с провайдером и сырым usage) + ``interaction_logs``."""
        try:
            request_id = str(uuid.uuid4())
            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider=provider,
                model=model,
                usage=usage,
                request_kind=request_kind,
                request_id=request_id,
                thread_id=thread_id,
                duration_sec=duration_sec,
                metadata=metadata,
            )
            pt, ct, tt, *_rest = extract_token_counts_and_extras(usage)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type=f"{provider}_{request_kind}_{model}",
                data={
                    "provider": provider,
                    "request_id": request_id,
                    "model": model,
                    "request_kind": request_kind,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                    "thread_id": thread_id,
                },
                source=provider,
                outcome="success",
            )

            logger.debug(
                "LLM metrics logged (%s/%s/%s): %s total tokens",
                provider,
                request_kind,
                model,
                tt,
            )

        except Exception as e:
            logger.error("❌ Failed to log LLM metrics: %s", e)

    # =====================================================
    # WHISPER: распознавание голоса/аудио/видео
    # =====================================================

    async def transcribe_voice(
        self,
        audio_file_path: str,
        user_id: int,
        duration_sec: Optional[int] = None,
    ) -> Optional[str]:
        """Распознаёт аудиофайл через Whisper."""
        start_time = datetime.now()
        path = audio_file_path
        compressed: Optional[str] = None

        try:
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            if size > _WHISPER_MAX_FILE_BYTES:
                logger.info(
                    "Whisper: %s MB > 24 MB — сжимаем перед отправкой",
                    size // (1024 * 1024),
                )
                compressed = await _compress_audio_for_whisper(path)
                if not compressed:
                    logger.warning(
                        "Whisper skip: file %s bytes exceeds API limit and compress failed",
                        size,
                    )
                    return None
                path = compressed
                size = os.path.getsize(path)
                if size > _WHISPER_MAX_FILE_BYTES:
                    logger.warning(
                        "Whisper skip: compressed file still %s bytes > 24 MB",
                        size,
                    )
                    return None

            with open(path, "rb") as audio_file:
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    prompt=(
                        "Если в аудио нет речи, просто верни [тишина]. "
                        "Если в аудио посторонние шумы, но не слышно слов, "
                        "ответить текстом [шум, слов не разобрать]"
                    ),
                    response_format="text",
                )

            processing_time = (datetime.now() - start_time).total_seconds()

            await self._log_llm_metrics(
                user_id=user_id,
                provider="openai",
                model="whisper-1",
                usage=None,
                request_kind="whisper_transcription",
                duration_sec=duration_sec,
                metadata={
                    "processing_time_sec": processing_time,
                    "audio_duration_sec": duration_sec,
                    "transcription_length": len(transcript) if transcript else 0,
                },
            )

            return transcript or None

        except Exception as e:
            logger.error(f"❌ Whisper transcription failed: {e}")
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="openai",
                event_type="whisper_error",
                data={"error": str(e), "duration_sec": duration_sec},
                source="openai",
                outcome="error",
            )
            return None
        finally:
            if compressed:
                try:
                    os.remove(compressed)
                except OSError:
                    pass

    async def transcribe_segments(
        self,
        audio_file_path: str,
        user_id: int,
        *,
        segment_sec: int = 600,
    ) -> list:
        """Whisper verbose_json с таймкодами; длинные файлы режутся на куски."""
        from course.speech import SpeechSegment

        size = os.path.getsize(audio_file_path) if os.path.isfile(audio_file_path) else 0
        paths: list[tuple[str, float]] = [(audio_file_path, 0.0)]
        tmp_dir = None
        if size > _WHISPER_MAX_FILE_BYTES or True:
            # всегда режем на 10-минутные куски, чтобы таймкоды были устойчивы
            tmp_dir = tempfile.mkdtemp(prefix="wseg_")
            pattern = os.path.join(tmp_dir, "seg_%03d.mp3")
            cmd = [
                "ffmpeg", "-y", "-i", audio_file_path,
                "-f", "segment", "-segment_time", str(int(segment_sec)),
                "-c:a", "libmp3lame", "-ar", "16000", "-ac", "1",
                pattern,
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=300)
                segs = sorted(
                    os.path.join(tmp_dir, f)
                    for f in os.listdir(tmp_dir)
                    if f.startswith("seg_") and f.endswith(".mp3")
                )
                if segs:
                    paths = [(p, i * float(segment_sec)) for i, p in enumerate(segs)]
            except Exception as e:
                logger.warning("ffmpeg segment for whisper: %s", e)

        out: list[SpeechSegment] = []
        try:
            for path, offset in paths:
                compressed = None
                try:
                    sz = os.path.getsize(path)
                    send = path
                    if sz > _WHISPER_MAX_FILE_BYTES:
                        compressed = await _compress_audio_for_whisper(path)
                        if compressed:
                            send = compressed
                    with open(send, "rb") as audio_file:
                        try:
                            resp = await self.client.audio.transcriptions.create(
                                model="whisper-1",
                                file=audio_file,
                                response_format="verbose_json",
                                timestamp_granularities=["segment"],
                            )
                        except Exception:
                            audio_file.seek(0)
                            resp = await self.client.audio.transcriptions.create(
                                model="whisper-1",
                                file=audio_file,
                                response_format="verbose_json",
                            )
                    segs = getattr(resp, "segments", None) or []
                    if isinstance(resp, dict):
                        segs = resp.get("segments") or []
                    for seg in segs:
                        if isinstance(seg, dict):
                            start = float(seg.get("start") or 0) + offset
                            end = float(seg.get("end") or start) + offset
                            text = str(seg.get("text") or "").strip()
                        else:
                            start = float(getattr(seg, "start", 0) or 0) + offset
                            end = float(getattr(seg, "end", start) or start) + offset
                            text = str(getattr(seg, "text", "") or "").strip()
                        if text:
                            out.append(SpeechSegment(start_sec=start, end_sec=end, text=text))
                    await self._log_llm_metrics(
                        user_id=user_id,
                        provider="openai",
                        model="whisper-1",
                        usage=None,
                        request_kind="whisper_segments",
                        metadata={"offset_sec": offset, "segments": len(segs)},
                    )
                finally:
                    if compressed:
                        try:
                            os.remove(compressed)
                        except OSError:
                            pass
        finally:
            if tmp_dir:
                for f in os.listdir(tmp_dir):
                    try:
                        os.unlink(os.path.join(tmp_dir, f))
                    except OSError:
                        pass
                try:
                    os.rmdir(tmp_dir)
                except OSError:
                    pass
        return out

    # =====================================================
    # VISION: описание фото
    # =====================================================

    async def describe_image(
        self,
        base64_image: str,
        user_id: int,
        prompt: Optional[str] = None,
    ) -> Optional[str]:
        """Получает описание изображения через Vision (gpt-4o-mini)."""
        start_time = datetime.now()

        try:
            if prompt is None:
                prompt = (
                    "Опиши подробно, что изображено на фото. "
                    "Если есть текст — извлеки его."
                )

            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                },
                            },
                        ],
                    }
                ],
                max_tokens=1000,
            )

            processing_time = (datetime.now() - start_time).total_seconds()
            description = response.choices[0].message.content

            if hasattr(response, "usage") and response.usage:
                await self._log_llm_metrics(
                    user_id=user_id,
                    provider="openai",
                    model="gpt-4o-mini",
                    usage=response.usage,
                    request_kind="vision_chat_completion",
                    duration_sec=None,
                    metadata={
                        "processing_time_sec": processing_time,
                        "description_length": len(description) if description else 0,
                    },
                )

            return description

        except Exception as e:
            logger.error(f"❌ Vision API failed: {e}")
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="openai",
                event_type="vision_error",
                data={"error": str(e)},
                source="openai",
                outcome="error",
            )
            return None

    # =====================================================
    # ТЕКСТ ПО ПРОМПТУ (scheduled mailing и прочие разовые генерации)
    # =====================================================

    async def complete_text_prompt(
        self,
        *,
        user_id: int,
        prompt: str,
        model: str = "gpt-4o-mini",
        max_tokens: int = 2048,
        request_kind: str = "scheduled_mailing_prompt",
    ) -> Optional[str]:
        """Разовое завершение чата без стриминга; логирует usage."""
        start_time = datetime.now()
        try:
            response = await self.client.chat.completions.create(
                model=model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            processing_time = (datetime.now() - start_time).total_seconds()
            text = (
                response.choices[0].message.content.strip()
                if response.choices and response.choices[0].message.content
                else ""
            )
            if hasattr(response, "usage") and response.usage:
                await self._log_llm_metrics(
                    user_id=user_id,
                    provider="openai",
                    model=model or "gpt-4o-mini",
                    usage=response.usage,
                    request_kind=request_kind,
                    duration_sec=None,
                    metadata={
                        "processing_time_sec": processing_time,
                        "output_length": len(text),
                    },
                )
            return text or None
        except Exception as e:
            logger.error("❌ complete_text_prompt failed: %s", e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="openai",
                event_type="complete_text_prompt_error",
                data={"error": str(e), "model": model},
                source="openai",
                outcome="error",
            )
            return None

    # =====================================================
    # UTILITY
    # =====================================================

    async def close(self) -> None:
        """Закрывает HTTP-клиент."""
        await self.client.close()
        logger.info("✅ OpenAI client closed")
