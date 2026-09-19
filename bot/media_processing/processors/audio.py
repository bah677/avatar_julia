"""
Обработчик аудиофайлов (музыка, подкасты) через Whisper API.
Адаптировано из старого проекта.
"""

import logging
from typing import Optional, Dict, Any
import subprocess
import os

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType
from bot.media_processing.config.settings import MEDIA_LIMITS, SUPPORTED_AUDIO_TYPES
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


class AudioProcessor(BaseProcessor):
    """Обработка аудиофайлов через Whisper"""
    
    def __init__(self, user_storage, openai_client: OpenAIClient):
        super().__init__(user_storage)
        self.openai_client = openai_client
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли файл аудио"""
        file_type = file_info.get('file_type')
        mime = file_info.get('mime_type', '')
        
        return file_type == 'audio' or mime in SUPPORTED_AUDIO_TYPES
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Транскрибирует аудиофайл через Whisper.
        """
        start_time = self._start_timer()
        converted_path = None
        transcribed_text = None
        
        try:
            duration = file_info.get('duration', 0)
            title = file_info.get('title', 'аудио')
            performer = file_info.get('performer', '')
            
            # Проверка лимита длительности
            max_duration = MEDIA_LIMITS['audio']['max_duration_sec']
            if duration > max_duration:
                logger.warning(f"⚠️ Аудио слишком длинное: {duration} сек > {max_duration}")
                return ProcessedMedia(
                    text=f"[аудио длиной {duration//60} мин (превышен лимит)]",
                    media_type=MediaType.AUDIO,
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
            transcribed_text = await self.openai_client.transcribe_voice(
                audio_file_path=file_path,
                user_id=user_id,
                duration_sec=duration
            )
            
            # Если не получилось - конвертируем
            if transcribed_text is None:
                logger.warning("⚠️ Direct transcription failed, trying conversion...")
                
                converted_path = await self._convert_audio(file_path)
                if not converted_path:
                    raise Exception("Audio conversion failed")
                
                transcribed_text = await self.openai_client.transcribe_voice(
                    audio_file_path=converted_path,
                    user_id=user_id,
                    duration_sec=duration
                )
            
            processing_time = self._end_timer(start_time)
            
            # Формируем источник
            if performer:
                source = f"{performer} - {title}"
            else:
                source = title
            
            # Формируем текст для сообщения
            if transcribed_text:
                text = f"[аудио {source}: {transcribed_text}]"
                confidence = 0.9
                has_text = True
            else:
                text = f"[аудио {source} (без речи)]"
                confidence = 0.5
                has_text = False
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.AUDIO,
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
                    'title': title,
                    'performer': performer,
                    'has_speech': bool(transcribed_text)
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"🎵 Аудио обработано: {source}, длительность={duration} сек")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Audio processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[аудиофайл (не удалось распознать)]",
                media_type=MediaType.AUDIO,
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