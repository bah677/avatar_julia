"""
Обработчик реакций.
"""

import logging
from typing import Optional, Dict, Any

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType

logger = logging.getLogger(__name__)


class ReactionProcessor(BaseProcessor):
    """Обработка реакций на сообщения"""
    
    # Словарь для перевода emoji в действие
    REACTION_ACTIONS = {
        '👍': 'понравилось',
        '❤️': 'полюбил',
        '🔥': 'восхитился',
        '🥰': 'обожает',
        '😁': 'развеселило',
        '🎉': 'празднует',
        '👎': 'не понравилось',
        '😢': 'огорчило',
        '😡': 'разозлило',
        '🤔': 'задумался',
    }
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли событие реакцией"""
        return file_info.get('file_type') == 'reaction'
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Преобразует реакцию в текст.
        """
        start_time = self._start_timer()
        
        try:
            emoji = file_info.get('emoji', '')
            is_added = file_info.get('added', True)
            message_id = file_info.get('target_message_id')
            
            action = self.REACTION_ACTIONS.get(emoji, f'отреагировал {emoji}')
            
            if is_added:
                text = f"[пользователь {action} на сообщение {message_id}]"
            else:
                text = f"[пользователь убрал реакцию {emoji} с сообщения {message_id}]"
            
            processing_time = self._end_timer(start_time)
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.REACTION,
                user_id=user_id,
                confidence=1.0,
                has_text=True,
                processing_time_ms=processing_time,
                metadata={
                    'emoji': emoji,
                    'action': action,
                    'is_added': is_added,
                    'target_message_id': message_id
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.debug(f"📝 Reaction: {text}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Reaction processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text=None,
                media_type=MediaType.REACTION,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=processing_time,
                metadata={'error': str(e)}
            )
            
            await self._log_processing(user_id, result, 'failed', error=str(e))
            return result