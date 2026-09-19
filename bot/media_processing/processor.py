"""
Главный диспетчер медиапроцессора.
Определяет тип сообщения и делегирует обработку соответствующему процессору.
"""

import logging
import asyncio
from typing import Optional, Dict, Any, List
from aiogram.types import Message

from config import config

from bot.features.rag_group_metadata import telegram_internal_message_link

from .models import ProcessedMedia, MediaType
from .downloader import FileDownloader
from .config.settings import GLOBAL_LIMITS

# Импорты процессоров
from .processors.voice import VoiceProcessor
from .processors.audio import AudioProcessor
from .processors.video import VideoProcessor
from .processors.video_note import VideoNoteProcessor
from .processors.photo import PhotoProcessor
from .processors.document import DocumentProcessor
from .processors.sticker import StickerProcessor
from .processors.location import LocationProcessor
from .processors.contact import ContactProcessor
from .processors.reaction import ReactionProcessor

from storage.user_storage import UserStorage
from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)


class MediaProcessor:
    """
    Основной процессор, определяет тип сообщения и делегирует специализированным.
    """
    
    def __init__(
        self,
        user_storage: UserStorage,
        openai_client: OpenAIClient,
        bot  # экземпляр бота для скачивания файлов
    ):
        self.user_storage = user_storage
        self.openai_client = openai_client
        self.bot = bot
        self.downloader = FileDownloader()
        
        # Инициализируем все процессоры
        self._processors = self._init_processors()
        
        logger.info(f"✅ MediaProcessor инициализирован с {len(self._processors)} процессорами")
    
    def _init_processors(self) -> List:
        """Инициализирует все специализированные процессоры"""
        return [
            VoiceProcessor(self.user_storage, self.openai_client),
            AudioProcessor(self.user_storage, self.openai_client),
            VideoProcessor(self.user_storage, self.openai_client),
            VideoNoteProcessor(self.user_storage, self.openai_client),
            PhotoProcessor(self.user_storage, self.openai_client),
            DocumentProcessor(self.user_storage),
            StickerProcessor(self.user_storage),
            LocationProcessor(self.user_storage),
            ContactProcessor(self.user_storage),
            ReactionProcessor(self.user_storage),
        ]
    
    async def process_message(
        self,
        message: Message,
        user_id: int,
        messages_row_id: Optional[int] = None,
    ) -> ProcessedMedia:
        """
        Определяет тип сообщения и обрабатывает соответствующим процессором.
        
        Args:
            message: сообщение от Telegram
            user_id: ID пользователя
            
        Returns:
            ProcessedMedia с текстом для дальнейшей обработки
        """
        start_time = asyncio.get_event_loop().time()

        # Group/channel path skips inbound user middleware — ensure FK parent for
        # media_inbound_files / token_usage / interaction_logs.
        if user_id and user_id > 0 and message.from_user:
            try:
                await self.user_storage.add_or_update_user(
                    {
                        "user_id": message.from_user.id,
                        "username": getattr(message.from_user, "username", None),
                        "first_name": getattr(message.from_user, "first_name", None),
                        "last_name": getattr(message.from_user, "last_name", None),
                        "language_code": getattr(message.from_user, "language_code", None),
                        "is_premium": getattr(message.from_user, "is_premium", False),
                    }
                )
            except Exception as e:
                logger.warning("ensure user before media process failed uid=%s: %s", user_id, e)
        
        # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ В ЗАВИСИМОСТИ ОТ ТИПА
        #await self._send_processing_notification(message)
        
        try:
            # Извлекаем информацию о файле
            file_info = self._extract_file_info(message)
            
            # Проверка размера файла
            if file_info.get('file_size', 0) > GLOBAL_LIMITS['max_file_size_bytes']:
                logger.warning(f"⚠️ Файл слишком большой: {file_info.get('file_size')} bytes")
                return ProcessedMedia(
                    text="[файл слишком большой для обработки]",
                    media_type=MediaType.UNKNOWN,
                    user_id=user_id,
                    confidence=0.0,
                    has_text=False,
                    processing_time_ms=int((asyncio.get_event_loop().time() - start_time) * 1000),
                    metadata={'error': 'file_too_large'}
                )
            
            # Ищем подходящий процессор
            for processor in self._processors:
                if await processor.can_process(file_info):
                    # Скачиваем файл если нужно
                    file_path = None
                    if file_info.get('needs_download'):
                        post_url = telegram_internal_message_link(message) or None
                        file_path = await self.downloader.download_file(
                            file_info['file_id'],
                            self.bot,
                            file_size=file_info.get('file_size'),
                            post_url=post_url,
                            filename_hint=file_info.get('file_name'),
                        )
                        if not file_path:
                            return ProcessedMedia(
                                text="[не удалось скачать файл]",
                                media_type=MediaType.UNKNOWN,
                                user_id=user_id,
                                confidence=0.0,
                                has_text=False,
                                processing_time_ms=int(
                                    (asyncio.get_event_loop().time() - start_time) * 1000
                                ),
                                metadata={
                                    "error": "download_failed",
                                    "file_size": file_info.get("file_size"),
                                },
                            )
                        if config.media_inbound_archive_enabled:
                            await self.user_storage.archive_inbound_media_file(
                                user_id=user_id,
                                chat_id=file_info.get('chat_id', message.chat.id),
                                telegram_message_id=file_info['message_id'],
                                file_unique_id=file_info.get('file_unique_id'),
                                file_id_at_capture=file_info['file_id'],
                                media_subtype=file_info.get('file_type', 'unknown'),
                                mime_type=file_info.get('mime_type'),
                                file_size=file_info.get('file_size'),
                                duration_sec=file_info.get('duration'),
                                source_path=file_path,
                                messages_row_id=messages_row_id,
                            )
                    
                    # Обрабатываем
                    try:
                        result = await processor.process(
                            file_path=file_path,
                            user_id=user_id,
                            file_info=file_info
                        )
                    finally:
                        # Очищаем временный файл
                        if file_path:
                            await self.downloader.cleanup_file(file_path)

                    self._merge_media_caption_into_processed(message, result)
                    
                    # Добавляем метаданные
                    result.processing_time_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
                    
                    # Обрезаем текст если слишком длинный
                    if result.text and len(result.text) > GLOBAL_LIMITS['max_result_length']:
                        result.text = result.text[:GLOBAL_LIMITS['max_result_length']] + "..."
                    
                    logger.info(f"✅ Медиа обработано: {result.media_type.value}, время={result.processing_time_ms}ms")
                    return result
            
            # Если нет подходящего процессора - возвращаем как текст
            text = message.text or message.caption or ""
            return ProcessedMedia(
                text=text,
                media_type=MediaType.TEXT if text else MediaType.UNKNOWN,
                user_id=user_id,
                confidence=1.0 if text else 0.0,
                has_text=bool(text),
                processing_time_ms=int((asyncio.get_event_loop().time() - start_time) * 1000),
                metadata={'caption': message.caption} if message.caption else {}
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка в MediaProcessor: {e}", exc_info=True)
            return ProcessedMedia(
                text="[ошибка обработки]",
                media_type=MediaType.UNKNOWN,
                user_id=user_id,
                confidence=0.0,
                has_text=False,
                processing_time_ms=int((asyncio.get_event_loop().time() - start_time) * 1000),
                metadata={'error': str(e)}
            )

    @staticmethod
    def _merge_media_caption_into_processed(message: Message, result: ProcessedMedia) -> None:
        """Добавляет подпись Telegram к тексту после обработки медиа (тикеты, агент, лог сообщения)."""
        caption = (message.caption or "").strip()
        if not caption:
            return
        body = (result.text or "").strip()
        if not body:
            result.text = caption
            result.has_text = True
            return
        if body.startswith(caption):
            return
        result.text = f"{caption}\n\n{body}"

    def _extract_file_info(self, message: Message) -> Dict[str, Any]:
        """
        Извлекает информацию о файле из сообщения.
        
        Args:
            message: сообщение от Telegram
            
        Returns:
            словарь с информацией о файле
        """
        info = {
            'message_id': message.message_id,
            'chat_id': message.chat.id,
            'date': message.date,
            'needs_download': False
        }
        
        # Проверяем все возможные типы
        if message.voice:
            info.update({
                'file_type': 'voice',
                'file_id': message.voice.file_id,
                'file_unique_id': message.voice.file_unique_id,
                'file_size': message.voice.file_size,
                'duration': message.voice.duration,
                'mime_type': 'audio/ogg',
                'needs_download': True
            })
        elif message.audio:
            info.update({
                'file_type': 'audio',
                'file_id': message.audio.file_id,
                'file_unique_id': message.audio.file_unique_id,
                'file_size': message.audio.file_size,
                'duration': message.audio.duration,
                'title': message.audio.title,
                'performer': message.audio.performer,
                'mime_type': message.audio.mime_type,
                'needs_download': True
            })
        elif message.video:
            info.update({
                'file_type': 'video',
                'file_id': message.video.file_id,
                'file_unique_id': message.video.file_unique_id,
                'file_size': message.video.file_size,
                'duration': message.video.duration,
                'width': message.video.width,
                'height': message.video.height,
                'mime_type': message.video.mime_type,
                'needs_download': True
            })
        elif message.video_note:
            info.update({
                'file_type': 'video_note',
                'file_id': message.video_note.file_id,
                'file_unique_id': message.video_note.file_unique_id,
                'file_size': message.video_note.file_size,
                'duration': message.video_note.duration,
                'length': message.video_note.length,
                'needs_download': True
            })
        elif message.photo:
            # Берем самое большое фото
            photo = message.photo[-1]
            info.update({
                'file_type': 'photo',
                'file_id': photo.file_id,
                'file_unique_id': photo.file_unique_id,
                'file_size': photo.file_size,
                'width': photo.width,
                'height': photo.height,
                'caption': message.caption,
                'needs_download': True
            })
        elif message.document:
            info.update({
                'file_type': 'document',
                'file_id': message.document.file_id,
                'file_unique_id': message.document.file_unique_id,
                'file_size': message.document.file_size,
                'file_name': message.document.file_name,
                'mime_type': message.document.mime_type,
                'caption': message.caption,
                'needs_download': True
            })
        elif message.sticker:
            info.update({
                'file_type': 'sticker',
                'file_id': message.sticker.file_id,
                'file_unique_id': message.sticker.file_unique_id,
                'file_size': message.sticker.file_size,
                'emoji': message.sticker.emoji,
                'set_name': message.sticker.set_name,
                'is_animated': message.sticker.is_animated,
                'is_video': message.sticker.is_video,
                'needs_download': False
            })
        elif message.location:
            info.update({
                'file_type': 'location',
                'latitude': message.location.latitude,
                'longitude': message.location.longitude,
                'needs_download': False
            })
        elif message.contact:
            info.update({
                'file_type': 'contact',
                'phone_number': message.contact.phone_number,
                'first_name': message.contact.first_name,
                'last_name': message.contact.last_name,
                'user_id': message.contact.user_id,
                'vcard': message.contact.vcard,
                'needs_download': False
            })
        elif message.text:
            info.update({
                'file_type': 'text',
                'text': message.text,
                'needs_download': False
            })
        
        # Определяем является ли сообщение командой
        if info.get('file_type') == 'text' and info.get('text', '').startswith('/'):
            info['is_command'] = True
        
        return info



    async def _send_processing_notification(self, message: Message):
        """Отправляет уведомление о начале обработки в зависимости от типа контента"""
        try:
            if message.text:
                await message.reply("📖 Читаю твоё сообщение и пишу ответ...")
                
            elif message.voice:
                await message.reply("🎧 Слушаю твоё голосовое сообщение...")
                
            elif message.audio:
                await message.reply("🎵 Слушаю аудиофайл...")
                
            elif message.video or message.video_note:
                await message.reply("📹 Смотрю видео...")
                
            elif message.photo:
                await message.reply("📸 Смотрю твоё изображение...")
                
            elif message.document:
                # Определяем тип документа по расширению или mime-типу
                file_name = message.document.file_name or ""
                if file_name.endswith('.pdf'):
                    await message.reply("📄 Читаю PDF документ...")
                elif file_name.endswith(('.doc', '.docx')):
                    await message.reply("📝 Читаю Word документ...")
                elif file_name.endswith('.txt'):
                    await message.reply("📃 Читаю текстовый файл...")
                else:
                    await message.reply("📎 Обрабатываю документ...")
                    
            elif message.sticker:
                await message.reply("🎨 Смотрю стикер...")
                
            elif message.location:
                await message.reply("📍 Определяю местоположение...")
                
            elif message.contact:
                await message.reply("📇 Обрабатываю контакт...")
                
            else:
                await message.reply("⏳ Обрабатываю ваше сообщение...")
                
        except Exception as e:
            logger.error(f"❌ Failed to send processing notification: {e}")        