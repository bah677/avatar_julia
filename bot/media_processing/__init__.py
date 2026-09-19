"""
Модуль для преобразования любых медиа в текст.
Встраивается как промежуточный слой между хендлерами и фичами.
"""

from .processor import MediaProcessor
from .models import ProcessedMedia, MediaType, EventCategory
from .downloader import FileDownloader

__all__ = [
    'MediaProcessor',
    'ProcessedMedia', 
    'MediaType',
    'EventCategory',
    'FileDownloader'
]