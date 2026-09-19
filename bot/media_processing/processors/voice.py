"""
Обработчик голосовых сообщений через Whisper API.
Адаптировано из старого проекта.
"""

import logging
from typing import Optional, Dict, Any
import subprocess
import os

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType
from bot.media_processing.config.settings import MEDIA_LIMITS
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


class VoiceProcessor(BaseProcessor):
    """Обработка голосовых сообщений через Whisper"""
    
    def __init__(self, user_storage, openai_client: OpenAIClient):
        super().__init__(user_storage)
        self.openai_client = openai_client
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли файл голосовым сообщением"""
        return file_info.get('file_type') == 'voice'
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Транскрибирует голосовое сообщение через Whisper.
        """
        start_time = self._start_timer()
        converted_path = None
        transcribed_text = None
        
        try:
            duration = file_info.get('duration', 0)
            
            # Проверка лимита длительности
            max_duration = MEDIA_LIMITS['voice']['max_duration_sec']
            if duration > max_duration:
                logger.warning(f"⚠️ Голосовое слишком длинное: {duration} сек > {max_duration}")
                return ProcessedMedia(
                    text=f"[голосовое сообщение длиной {duration//60} мин (превышен лимит)]",
                    media_type=MediaType.VOICE,
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
            
            # Пробуем транскрибировать напрямую
            logger.info("🔄 Attempting direct transcription...")
            transcribed_text = await self.openai_client.transcribe_voice(
                audio_file_path=file_path,
                user_id=user_id,
                duration_sec=duration
            )
            
            # 🔥 ПРОВЕРЯЕМ, ЧТО ВЕРНУЛОСЬ, А НЕ ЛОВИМ ИСКЛЮЧЕНИЕ
            if transcribed_text is None:
                logger.warning("⚠️ Direct transcription returned None, trying conversion...")
                
                converted_path = await self._convert_audio(file_path)
                if not converted_path:
                    raise Exception("Audio conversion failed")
                
                logger.info(f"✅ Audio converted to: {converted_path}")
                
                transcribed_text = await self.openai_client.transcribe_voice(
                    audio_file_path=converted_path,
                    user_id=user_id,
                    duration_sec=duration
                )
                
                if transcribed_text is None:
                    logger.error("❌ Transcription of converted file also failed")
                    transcribed_text = None
                else:
                    logger.info("✅ Transcription of converted file successful")
            else:
                logger.info("✅ Direct transcription successful")
            
            processing_time = self._end_timer(start_time)
            
            # Формируем результат
            if transcribed_text:
                text = f"[голосовое: {transcribed_text}]"
                confidence = 0.9
                has_text = True
            else:
                text = "[голосовое сообщение (речь не распознана)]"
                confidence = 0.0
                has_text = False
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.VOICE,
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
                    'transcription_length': len(transcribed_text) if transcribed_text else 0
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"🎤 Голосовое обработано: {duration} сек, длина={len(transcribed_text) if transcribed_text else 0}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Voice processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[голосовое сообщение (ошибка распознавания)]",
                media_type=MediaType.VOICE,
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
            # Очищаем конвертированный файл
            if converted_path and os.path.exists(converted_path):
                os.unlink(converted_path)
                logger.debug(f"🧹 Cleaned up converted file: {converted_path}")

    async def _convert_audio(self, input_path: str) -> Optional[str]:
        """
        Конвертирует аудио в формат, который точно примет Whisper.
        Возвращает путь к сконвертированному файлу.
        """
        try:
            output_path = input_path + "_converted.wav"
            
            # Конвертируем в WAV
            result = subprocess.run([
                'ffmpeg', '-i', input_path,
                '-ar', '16000',
                '-ac', '1',
                '-c:a', 'pcm_s16le',
                '-y',
                output_path
            ], capture_output=True, timeout=30)
            
            if result.returncode == 0 and os.path.exists(output_path):
                return output_path
            else:
                logger.error(f"❌ FFmpeg conversion error: {result.stderr.decode()}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Audio conversion failed: {e}")
            return None