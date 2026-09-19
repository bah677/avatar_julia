"""
Модели данных для медиапроцессора.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime


class MediaType(str, Enum):
    """Типы медиа, которые может обработать бот"""
    TEXT = "text"
    VOICE = "voice"
    AUDIO = "audio"
    VIDEO = "video"
    VIDEO_NOTE = "video_note"  # кружочек
    PHOTO = "photo"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACT = "contact"
    REACTION = "reaction"
    CALLBACK = "callback"
    COMMAND = "command"
    UNKNOWN = "unknown"


class EventCategory(str, Enum):
    """Категории событий для логирования"""
    MESSAGE = "message"
    CALLBACK = "callback"
    COMMAND = "command"
    MEDIA = "media"
    OPENAI = "openai"
    SYSTEM = "system"
    REACTION = "reaction"


@dataclass
class ProcessedMedia:
    """
    Результат обработки медиа.
    Содержит текст для передачи в фичи и метаданные для логирования.
    """
    # Основной текст для передачи в фичи
    text: Optional[str]
    
    # Тип исходного медиа
    media_type: MediaType
    
    # ID пользователя
    user_id: int
    
    # Метаданные обработки
    confidence: float = 1.0  # 0-1 насколько удачно распознано
    has_text: bool = True  # есть ли распознанный текст
    
    # Технические данные
    processing_time_ms: int = 0
    file_id: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    duration_sec: Optional[int] = None
    
    # Для OpenAI
    tokens_used: Optional[int] = None
    model_used: Optional[str] = None
    
    # Специфичные для медиа данные
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Сырые данные от Telegram (для логирования)
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class ProcessingResult:
    """Результат обработки с дополнительной информацией для БД"""
    processed: ProcessedMedia
    message_id: int  # ID в таблице messages
    telegram_message_id: int
    chat_id: int
    created_at: datetime


__all__ = [
    'MediaType',
    'EventCategory',
    'ProcessedMedia',
    'ProcessingResult'
]