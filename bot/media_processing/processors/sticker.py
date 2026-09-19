"""
Обработчик стикеров - определение emoji и названия набора.
Адаптировано из старого проекта.
"""

import logging
from typing import Optional, Dict, Any

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType

logger = logging.getLogger(__name__)


class StickerProcessor(BaseProcessor):
    """Обработка стикеров - emoji и название набора"""
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли сообщение стикером"""
        return file_info.get('file_type') == 'sticker'
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Возвращает описание стикера.
        """
        start_time = self._start_timer()
        
        try:
            emoji = file_info.get('emoji', '🖼️')
            set_name = file_info.get('set_name', '')
            
            processing_time = self._end_timer(start_time)
            
            # Формируем текст
            if emoji and set_name:
                text = f"[стикер: {emoji} из набора {set_name}]"
                confidence = 1.0
            elif emoji:
                text = f"[стикер: {emoji}]"
                confidence = 1.0
            elif set_name:
                text = f"[стикер из набора: {set_name}]"
                confidence = 0.9
            else:
                text = "[стикер]"
                confidence = 0.8
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.STICKER,
                user_id=user_id,
                confidence=confidence,
                has_text=True,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                file_size=file_info.get('file_size'),
                metadata={
                    'emoji': emoji,
                    'set_name': set_name,
                    'is_animated': file_info.get('is_animated', False),
                    'is_video': file_info.get('is_video', False)
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"🎨 Стикер обработан: {emoji} из набора {set_name}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Sticker processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[стикер]",
                media_type=MediaType.STICKER,
                user_id=user_id,
                confidence=0.5,
                has_text=True,
                processing_time_ms=processing_time,
                file_id=file_info.get('file_id'),
                metadata={'error': str(e)}
            )
            
            await self._log_processing(user_id, result, 'failed', error=str(e))
            return result