"""
Обработчик всех входящих сообщений с медиапроцессором.
"""

import logging
import asyncio
from typing import Optional

from aiogram import Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.filters.private_only import CALLBACK_PRIVATE_CHAT, PRIVATE_CHAT
from bot.features.base import FeatureManager
from bot.media_processing import MediaProcessor, ProcessedMedia
from bot.logging.message_copier import MessageCopier
from bot.logging.interaction_logger import InteractionLogger
from bot.utils.chat_actions import media_processing_chat_action

logger = logging.getLogger(__name__)


def text_for_feature_route(processed: ProcessedMedia, message: Message) -> str:
    """Текст для маршрута в фичи: приоритет распознанному, иначе text/caption сообщения."""
    if processed.text and str(processed.text).strip():
        return str(processed.text).strip()
    if message.text:
        return message.text.strip()
    if message.caption:
        return message.caption.strip()
    return ""


# =====================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ МАРШРУТИЗАЦИИ
# =====================================================

async def route_message_to_feature(
    message: Message,
    state: FSMContext,
    processed: ProcessedMedia,
    message_id: int,
    feature_manager: FeatureManager
):
    """Маршрутизирует сообщение в соответствующую фичу."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    text = text_for_feature_route(processed, message)
    
    logger.info(f"🔄 Routing message for user={user_id}, state={current_state}, text_length={len(text)}")
    
    if current_state:
        current_state_lower = current_state.lower()

        if "support" in current_state_lower:
            feature = feature_manager.get("support")
            await feature.handle_message(message, state, text)
        else:
            feature = feature_manager.get("messaging")
            await feature.handle_chat_message(message, state, text, message_id)
    else:
        feature = feature_manager.get("messaging")
        await feature.handle_chat_message(message, state, text, message_id)


# =====================================================
# ОСНОВНОЙ КЛАСС ОБРАБОТЧИКОВ
# =====================================================

class MessageHandlers:
    """Обработчик всех типов сообщений"""
    
    def __init__(
        self, 
        dp: Dispatcher, 
        feature_manager: FeatureManager,
        media_processor: MediaProcessor,
        message_copier: MessageCopier,
        interaction_logger: InteractionLogger
    ):
        self.dp = dp
        self.features = feature_manager
        self.media_processor = media_processor
        self.message_copier = message_copier
        self.interaction_logger = interaction_logger
        self.bot = None
    
    def set_bot(self, bot):
        """Устанавливает ссылку на бота для доступа к очередям"""
        self.bot = bot
    
    def register_handlers(self):
        """Регистрирует все обработчики"""
        self.dp.message.register(self._universal_message_handler, PRIVATE_CHAT)
        self.dp.callback_query.register(
            self._callback_handler,
            CALLBACK_PRIVATE_CHAT,
            ~F.data.startswith("payment_"),
        )
        self.dp.edited_message.register(
            self._edited_message_handler, PRIVATE_CHAT
        )
        self.dp.message_reaction.register(
            self._reaction_handler, PRIVATE_CHAT
        )
        logger.info("✅ Message handlers registered")
    
    async def _universal_message_handler(
        self,
        message: Message,
        state: FSMContext,
        logged_message_id: Optional[int] = None,
    ):
        """
        Обрабатывает текстовые сообщения и медиа.
        Логирование уже выполнено в middleware (InboundLoggingMiddleware),
        id записи в БД пробрасывается через ``logged_message_id``.
        """
        user_id = message.from_user.id

        try:
            tg_bot = self.bot.bot if self.bot else None
            if tg_bot:
                async with media_processing_chat_action(tg_bot, message):
                    processed = await self.media_processor.process_message(
                        message, user_id, logged_message_id
                    )
            else:
                processed = await self.media_processor.process_message(
                    message, user_id, logged_message_id
                )
            # Если middleware успел сохранить запись — обновим в ней content
            # после распознавания. Если по какой-то причине не сохранил — тихо
            # пропустим (запись просто останется с исходным текстом из middleware).
            if logged_message_id is not None:
                if processed.text:
                    await self.message_copier.update_message_content(
                        message_id=logged_message_id,
                        content=processed.text,
                        metadata={
                            'confidence': processed.confidence,
                            'media_type': processed.media_type.value,
                            'processing_time_ms': processed.processing_time_ms,
                            **processed.metadata,
                        },
                    )
                else:
                    error_text = f"[{processed.media_type.value} (не удалось обработать)]"
                    await self.message_copier.update_message_content(
                        message_id=logged_message_id,
                        content=error_text,
                        metadata={
                            'confidence': 0.0,
                            'media_type': processed.media_type.value,
                            'processing_time_ms': processed.processing_time_ms,
                            'error': 'recognition_failed',
                            **processed.metadata,
                        },
                    )
            
            # Если нет текста - сообщаем пользователю
            #if not processed.has_text and processed.media_type != MediaType.TEXT:
                #await message.answer("📝 Пожалуйста, отправьте текстовое сообщение")
            #    return
            
            # Добавляем в очередь на обработку
            if self.bot:
                await self.bot.add_to_queue(user_id, {
                    'message': message,
                    'processed': processed,
                    'message_id': None  # message_id больше не нужен здесь
                })
            else:
                # Аварийный случай
                await route_message_to_feature(
                    message, state, processed, None, self.features
                )
            
        except Exception as e:
            logger.error(f"❌ Error in universal message handler: {e}", exc_info=True)
    
    async def _callback_handler(self, callback: CallbackQuery, state: FSMContext):
        """
        Обрабатывает callback'и, не пойманные узкими хендлерами (платежи, FAQ и т.д.).
        Регистрируйте специфичные callback'и отдельными фичами *до* MessageHandlers.
        """
        logger.warning("⚠️ Unhandled callback: %s", callback.data)
        await callback.answer("❌ Действие недоступно", show_alert=True)
    
    async def _edited_message_handler(self, message: Message, state: FSMContext):
        """
        Обрабатывает отредактированные сообщения.
        Логирование уже выполнено в middleware.
        """
        logger.debug(f"✏️ Edited message from user {message.from_user.id}")
    
    async def _reaction_handler(self, message_reaction, state: FSMContext):
        """
        Обрабатывает реакции.
        Логирование уже выполнено в middleware.
        """
        logger.debug(f"👍 Reaction from user {message_reaction.user.id}")