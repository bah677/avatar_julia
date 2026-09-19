"""
Обработчик видеокружочков (video_note) - извлечение аудио и транскрибация через Whisper.
Адаптировано из video.py.
"""

import os
import tempfile
import subprocess
import logging
from typing import Optional, Dict, Any

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType
from bot.media_processing.config.settings import MEDIA_LIMITS
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


class VideoNoteProcessor(BaseProcessor):
    """Обработка видеокружочков - извлечение аудио и транскрибация через Whisper"""
    
    def __init__(self, user_storage, openai_client: OpenAIClient):
        super().__init__(user_storage)
        self.openai_client = openai_client
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли файл видеокружочком"""
        return file_info.get('file_type') == 'video_note'
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Извлекает аудио из кружочка и транскрибирует через Whisper.
        """
        start_time = self._start_timer()
        audio_path = None
        
        try:
            duration = file_info.get('duration', 0)
            
            # Проверка лимита длительности (кружочки и так ограничены Telegram до 60 сек)
            max_duration = MEDIA_LIMITS['video_note']['max_duration_sec']
            if duration > max_duration:
                logger.warning(f"⚠️ Кружочек слишком длинный: {duration} сек > {max_duration}")
                return ProcessedMedia(
                    text=f"[кружочек длиной {duration} сек (превышен лимит)]",
                    media_type=MediaType.VIDEO_NOTE,
                    user_id=user_id,
                    confidence=0.0,
                    has_text=False,
                    processing_time_ms=self._end_timer(start_time),
                    file_id=file_info.get('file_id'),
                    file_size=file_info.get('file_size'),
                    duration_sec=duration,
                    metadata={'error': 'duration_limit_exceeded'}
                )
            
            if not file_path:
                raise ValueError("No file path provided")
            
            # Создаем временный файл для аудио
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_audio:
                audio_path = tmp_audio.name
            
            # Извлекаем аудио через ffmpeg
            result = subprocess.run([
                'ffmpeg', '-i', file_path,
                '-vn',  # без видео
                '-acodec', 'libmp3lame',
                '-ar', '16000',  # частота для Whisper
                '-ac', '1',  # моно
                '-y',  # перезаписывать
                audio_path
            ], capture_output=True, timeout=30)  # меньший таймаут для кружочков
            
            if result.returncode != 0:
                logger.error(f"❌ FFmpeg error: {result.stderr.decode()}")
                raise Exception(f"FFmpeg failed: {result.stderr.decode()}")
            
            # Транскрибируем аудио
            transcribed_text = await self.openai_client.transcribe_voice(
                audio_file_path=audio_path,
                user_id=user_id,
                duration_sec=duration
            )
            
            processing_time = self._end_timer(start_time)
            
            # Формируем текст для сообщения
            if transcribed_text:
                text = f"[кружочек с речью: {transcribed_text}]"
                confidence = 0.9
                has_text = True
            else:
                text = "[кружочек (без звука)]"
                confidence = 0.5
                has_text = False
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.VIDEO_NOTE,
                user_id=user_id,
                confidence=confidence,
                has_text=has_text,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                duration_sec=duration,
                model_used='whisper-1',
                metadata={
                    'duration_sec': duration,
                    'has_speech': bool(transcribed_text)
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"📹 Кружочек обработан: {duration} сек, есть речь={bool(transcribed_text)}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Video note processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[кружочек (ошибка обработки)]",
                media_type=MediaType.VIDEO_NOTE,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                duration_sec=file_info.get('duration', 0),
                metadata={'error': str(e)}
            )
            
            await self._log_processing(user_id, result, 'failed', error=str(e))
            return result
            
        finally:
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)