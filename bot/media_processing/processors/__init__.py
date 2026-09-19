"""
Пакет специализированных процессоров для разных типов медиа.
"""

from .base import BaseProcessor
from .voice import VoiceProcessor
from .audio import AudioProcessor
from .video import VideoProcessor
from .video_note import VideoNoteProcessor
from .photo import PhotoProcessor
from .document import DocumentProcessor
from .sticker import StickerProcessor
from .location import LocationProcessor
from .contact import ContactProcessor
from .reaction import ReactionProcessor

__all__ = [
    'BaseProcessor',
    'VoiceProcessor',
    'AudioProcessor', 
    'VideoProcessor',
    'VideoNoteProcessor',
    'PhotoProcessor',
    'DocumentProcessor',
    'StickerProcessor',
    'LocationProcessor',
    'ContactProcessor',
    'ReactionProcessor'
]