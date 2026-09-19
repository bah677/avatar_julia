"""
Базовый класс для всех процессоров.
"""

from abc import abstractmethod
import logging
import asyncio
from typing import Optional, Dict, Any

from bot.media_processing.interface import MediaProcessorInterface
from bot.media_processing.models import ProcessedMedia, MediaType
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)


class BaseProcessor(MediaProcessorInterface):
    """
    Базовый класс с общей логикой для всех процессоров.
    """
    
    def __init__(self, user_storage: UserStorage):
        self.user_storage = user_storage
    
    def _start_timer(self) -> float:
        """Начинает замер времени"""
        return asyncio.get_event_loop().time()
    
    def _end_timer(self, start: float) -> int:
        """Заканчивает замер и возвращает время в ms"""
        return int((asyncio.get_event_loop().time() - start) * 1000)
    
    @abstractmethod
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """Проверяет, может ли процессор обработать данный тип"""
        pass
    
    @abstractmethod
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """Обрабатывает файл"""
        pass
    
    async def _log_processing(
        self,
        user_id: int,
        result: ProcessedMedia,
        status: str = 'completed',
        error: Optional[str] = None
    ):
        """Логирует результат обработки"""
        try:
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category='media',
                event_type=f'{result.media_type.value}_{status}',
                processing_time_ms=result.processing_time_ms,
                data={
                    'media_type': result.media_type.value,
                    'confidence': result.confidence,
                    'has_text': result.has_text,
                    'file_size': result.file_size,
                    'duration_sec': result.duration_sec,
                    'error': error,
                    **result.metadata
                },
                source='media_processor',
                outcome=status,
            )
        except Exception as e:
            logger.error(f"❌ Ошибка логирования обработки: {e}")