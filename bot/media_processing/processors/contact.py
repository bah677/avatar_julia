"""
Обработчик контактов - преобразование в текст.
Адаптировано из старого проекта.
"""

import logging
from typing import Optional, Dict, Any

from bot.media_processing.processors.base import BaseProcessor
from bot.media_processing.models import ProcessedMedia, MediaType

logger = logging.getLogger(__name__)


class ContactProcessor(BaseProcessor):
    """Обработка контактов - формирование текстового описания"""
    
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, является ли сообщение контактом"""
        return file_info.get('file_type') == 'contact'
    
    def _mask_phone(self, phone: str) -> str:
        """Маскирует телефон для приватности"""
        if not phone:
            return ""
        if len(phone) <= 4:
            return phone
        # Оставляем первые 4 цифры, остальные заменяем звездочками
        return phone[:4] + '*' * (len(phone) - 4)
    
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Преобразует контакт в текст.
        """
        start_time = self._start_timer()
        
        try:
            phone = file_info.get('phone_number', '')
            first_name = file_info.get('first_name', '')
            last_name = file_info.get('last_name', '')
            contact_id = file_info.get('user_id')
            
            # Формируем имя
            name_parts = []
            if first_name:
                name_parts.append(first_name)
            if last_name:
                name_parts.append(last_name)
            full_name = ' '.join(name_parts) if name_parts else 'неизвестный'
            
            processing_time = self._end_timer(start_time)
            
            # Формируем текст с маскированным телефоном
            if phone:
                masked_phone = self._mask_phone(phone)
                text = f"[контакт: {full_name}, телефон: {masked_phone}]"
            else:
                text = f"[контакт: {full_name}]"
            
            result = ProcessedMedia(
                text=text,
                media_type=MediaType.CONTACT,
                user_id=user_id,
                confidence=1.0,
                has_text=True,
                processing_time_ms=processing_time,
                metadata={
                    'first_name': first_name,
                    'last_name': last_name,
                    'has_phone': bool(phone),
                    'contact_user_id': contact_id
                }
            )
            
            await self._log_processing(user_id, result, 'completed')
            
            logger.info(f"📇 Контакт обработан: {full_name}, есть телефон={bool(phone)}")
            return result
            
        except Exception as e:
            processing_time = self._end_timer(start_time)
            logger.error(f"❌ Contact processing failed: {e}", exc_info=True)
            
            result = ProcessedMedia(
                text="[контакт (ошибка обработки)]",
                media_type=MediaType.CONTACT,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=processing_time,
                metadata={'error': str(e)}
            )
            
            await self._log_processing(user_id, result, 'failed', error=str(e))
            return result