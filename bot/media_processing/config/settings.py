"""
Конфигурация медиапроцессора.
Все лимиты и настройки в одном месте.
"""

from typing import Dict, Any

# =====================================================
# ОБЩИЕ ЛИМИТЫ
# =====================================================

GLOBAL_LIMITS = {
    # Максимальный размер файла в байтах (300 MB)
    'max_file_size_bytes': 300 * 1024 * 1024,
    
    # Максимальное время обработки в секундах
    'max_processing_time_sec': 120,
    
    # Максимальная длина итогового текста (после медиапроцессора)
    'max_result_length': 400_000,
}

# =====================================================
# ЛИМИТЫ ПО ТИПАМ МЕДИА
# =====================================================

MEDIA_LIMITS = {
    'voice': {
        'max_duration_sec': 120 * 60,  # 120 минут
        'description': 'Голосовые сообщения'
    },
    'audio': {
        'max_duration_sec': 120 * 60,  # 120 минут
        'description': 'Аудиофайлы (музыка, подкасты)'
    },
    'video': {
        'max_duration_sec': 120 * 60,  # 120 минут
        'description': 'Видеофайлы'
    },
    'video_note': {
        'max_duration_sec': 60,  # 1 минута (ограничение Telegram)
        'description': 'Видеокружочки'
    },
    'youtube': {
        'max_duration_sec': 4 * 60 * 60,  # 4 часа
        'description': 'YouTube-видео (аудиодорожка)'
    },
    'photo': {
        'max_resolution_mp': 20,  # 20 мегапикселей
        'description': 'Фотографии'
    },
    'document': {
        'max_pages': 100,  # для PDF
        'description': 'Документы'
    }
}

# =====================================================
# ЛИМИТЫ ПО ИСПОЛЬЗОВАНИЮ (дневные)
# =====================================================

USAGE_LIMITS = {
    'whisper_minutes_per_day': 100,  # минут на пользователя в день
    'vision_images_per_day': 50,      # фото на пользователя в день
}

# =====================================================
# ПУТИ ДЛЯ ВРЕМЕННЫХ ФАЙЛОВ
# =====================================================

TEMP_FILE_CONFIG = {
    'base_dir': '/tmp/club_bot',
    'cleanup_age_hours': 24,  # удалять файлы старше 24 часов
}

# =====================================================
# ПОДДЕРЖИВАЕМЫЕ ТИПЫ ДОКУМЕНТОВ
# =====================================================

SUPPORTED_DOCUMENT_TYPES = {
    # PDF
    'application/pdf': {'extractor': 'pdf', 'description': 'PDF документ'},
    
    # Microsoft Word
    'application/msword': {'extractor': 'doc', 'description': 'Word документ'},
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': {'extractor': 'docx', 'description': 'Word документ'},
    
    # Текстовые
    'text/plain': {'extractor': 'txt', 'description': 'Текстовый файл'},
    'text/markdown': {'extractor': 'txt', 'description': 'Markdown файл'},
    'text/html': {'extractor': 'html', 'description': 'HTML файл'},
    
    # RTF
    'application/rtf': {'extractor': 'rtf', 'description': 'RTF документ'},
    
    # OpenDocument
    'application/vnd.oasis.opendocument.text': {'extractor': 'odt', 'description': 'OpenDocument текст'},
}

# =====================================================
# ПОДДЕРЖИВАЕМЫЕ АУДИО MIME ТИПЫ
# =====================================================

SUPPORTED_AUDIO_TYPES = [
    'audio/mpeg',  # mp3
    'audio/mp4',   # m4a
    'audio/wav',
    'audio/x-wav',
    'audio/ogg',
    'audio/aac',
    'audio/flac',
    'audio/x-m4a',
    'audio/x-mp3',
    'audio/mp3'
]

# =====================================================
# ПОДДЕРЖИВАЕМЫЕ ВИДЕО MIME ТИПЫ
# =====================================================

SUPPORTED_VIDEO_TYPES = [
    'video/mp4',
    'video/avi',
    'video/mov',
    'video/mkv',
    'video/webm',
    'video/quicktime',
    'video/x-matroska'
]