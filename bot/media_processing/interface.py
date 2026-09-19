"""
Интерфейсы для медиапроцессоров.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from .models import ProcessedMedia


class MediaProcessorInterface(ABC):
    """Базовый интерфейс для всех процессоров медиа"""
    
    @abstractmethod
    async def can_process(self, file_info: Dict[str, Any]) -> bool:
        """
        Проверяет, может ли процессор обработать данный тип файла.
        
        Args:
            file_info: информация о файле из _extract_file_info
            
        Returns:
            True если может обработать
        """
        pass
    
    @abstractmethod
    async def process(
        self,
        file_path: Optional[str],
        user_id: int,
        file_info: Dict[str, Any]
    ) -> ProcessedMedia:
        """
        Обрабатывает файл и возвращает текст.
        
        Args:
            file_path: путь к скачанному файлу (если нужен)
            user_id: ID пользователя
            file_info: информация о файле
            
        Returns:
            ProcessedMedia с результатом
        """
        pass


class DownloaderInterface(ABC):
    """Интерфейс для скачивания файлов"""
    
    @abstractmethod
    async def download_file(self, file_id: str, bot) -> Optional[str]:
        """
        Скачивает файл по file_id во временную директорию.
        
        Args:
            file_id: Telegram file_id
            bot: экземпляр бота для скачивания
            
        Returns:
            путь к скачанному файлу или None
        """
        pass
    
    @abstractmethod
    async def cleanup_file(self, file_path: str):
        """
        Удаляет временный файл.
        
        Args:
            file_path: путь к файлу
        """
        pass