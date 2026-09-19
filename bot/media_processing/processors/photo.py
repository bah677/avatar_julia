"""
Обработчик фотографий - распознавание текста через Vision API.
Адаптировано из старого проекта.
"""

import os
import base64
import logging
from typing import Optional, Dict, Any

import aiofiles

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType
from bot.media_processing.config.settings import MEDIA_LIMITS, USAGE_LIMITS
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


class PhotoProcessor(BaseProcessor):
    """Обработка фотографий - извлечение текста через Vision API"""
    
    def __init__(self, user_storage, openai_client: OpenAIClient):
        super().__init__(user_storage)
        self.openai_client = openai_client
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли файл изображением"""
        return file_info.get('file_type') == 'photo'
    
    async def _check_daily_limit(self, user_id: int) -> bool:
        """Проверяет дневной лимит на Vision API"""
        try:
            # Считаем количество обработанных фото за сегодня
            today_start = "CURRENT_DATE"
            async with self.user_storage.db.get_connection() as conn:
                count = await conn.fetchval(
                    f"""
                    SELECT COUNT(*) FROM token_usage 
                    WHERE user_id = $1 
                    AND created_date >= {today_start}
                    AND model LIKE '%vision%'
                    """,
                    user_id
                )
                return (count or 0) < USAGE_LIMITS['vision_images_per_day']
        except Exception as e:
            logger.error(f"❌ Ошибка проверки лимита Vision: {e}")
            return True  # В случае ошибки пропускаем
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Извлекает текст из изображения через Vision API.
        """
        start_time = self._start_timer()
        
        try:
            # Проверка дневного лимита
            if not await self._check_daily_limit(user_id):
                logger.warning(f"⚠️ Дневной лимит Vision для user_id={user_id} исчерпан")
                return ProcessedMedia(
                    text="[фото (достигнут дневной лимит обработки)]",
                    media_type=MediaType.PHOTO,
                    user_id=user_id,
                    confidence=0.0,
                    has_text=False,
                    processing_time_ms=self._end_timer(start_time),
                    file_id=file_info.get('file_id'),
                    file_size=file_info.get('file_size'),
                    metadata={'error': 'daily_limit_exceeded'}
                )
            
            if not file_path:
                raise ValueError("No file path provided")
            
            # Конвертируем изображение в base64
            async with aiofiles.open(file_path, 'rb') as f:
                image_data = await f.read()
                base64_image = base64.b64encode(image_data).decode('utf-8')
            
            # Получаем описание через Vision API
            description = await self.openai_client.describe_image(
                base64_image=base64_image,
                user_id=user_id
            )
            
            processing_time = self._end_timer(start_time)
            
            # Формируем результат
            if description:
                text = f"[фото: {description}]"
                confidence = 0.9
                has_text = True
            else:
                text = "[фото (не удалось распознать)]"
                confidence = 0.0
                has_text = False
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.PHOTO,
                user_id=user_id,
                confidence=confidence,
                has_text=has_text,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                model_used='gpt-4-vision',
                metadata={
                    'width': file_info.get('width'),
                    'height': file_info.get('height'),
                    'has_caption': bool(file_info.get('caption'))
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"📸 Фото обработано: длина описания={len(description) if description else 0}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Photo processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[фото (ошибка обработки)]",
                media_type=MediaType.PHOTO,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                metadata={'error': str(e)}
            )
            
            await self._log_processing(user_id, result, 'failed', error=str(e))
            return result