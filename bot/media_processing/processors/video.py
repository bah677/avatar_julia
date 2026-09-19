"""
Обработчик видео - извлечение аудио и транскрибация через Whisper.
Адаптировано из старого проекта.
"""

import os
import tempfile
import subprocess
import logging
from typing import Optional, Dict, Any

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType
from bot.media_processing.config.settings import MEDIA_LIMITS, SUPPORTED_VIDEO_TYPES
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


class VideoProcessor(BaseProcessor):
    """Обработка видео - извлечение аудио и транскрибация через Whisper"""
    
    def __init__(self, user_storage, openai_client: OpenAIClient):
        super().__init__(user_storage)
        self.openai_client = openai_client
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли файл видео"""
        file_type = file_info.get('file_type')
        mime = file_info.get('mime_type', '')
        
        return file_type in ['video', 'video_note'] or mime in SUPPORTED_VIDEO_TYPES
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Извлекает аудио из видео и транскрибирует через Whisper.
        """
        start_time = self._start_timer()
        audio_path = None
        
        try:
            duration = file_info.get('duration', 0)
            
            # Проверка лимита длительности
            max_duration = MEDIA_LIMITS['video']['max_duration_sec']
            if duration > max_duration:
                logger.warning(f"⚠️ Видео слишком длинное: {duration} сек > {max_duration}")
                return ProcessedMedia(
                    text=f"[видео длиной {duration//60} мин (превышен лимит)]",
                    media_type=MediaType.VIDEO,
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
            ], capture_output=True, timeout=120)
            
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
                text = f"[видео содержит речь: {transcribed_text}]"
                confidence = 0.9
                has_text = True
            else:
                text = f"[видео (без речи, {duration} сек)]"
                confidence = 0.5
                has_text = False
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.VIDEO,
                user_id=user_id,
                confidence=confidence,
                has_text=has_text,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                mime_type=file_info.get('mime_type'),
                duration_sec=duration,
                model_used='whisper-1',
                metadata={
                    'duration_sec': duration,
                    'width': file_info.get('width'),
                    'height': file_info.get('height'),
                    'has_speech': bool(transcribed_text)
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"🎬 Видео обработано: {duration} сек, есть речь={bool(transcribed_text)}")
            return result
            
        except subprocess.TimeoutExpired:
            processing_time = self._end_timer(start_time)
            logger.error("❌ FFmpeg timeout")
            
            result = ProcessedMedia(
                text="[видео (обработка заняла слишком много времени)]",
                media_type=MediaType.VIDEO,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                duration_sec=file_info.get('duration', 0),
                metadata={'error': 'timeout'}
            )
            
            await self._log_processing(user_id, result, 'failed', error='timeout')
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Video processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[видео (ошибка обработки)]",
                media_type=MediaType.VIDEO,
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
            # Очищаем временный файл
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)