# bot/features/media_id_helper.py
import logging

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.filters.private_only import PRIVATE_CHAT
from bot.features.base import BaseFeature

logger = logging.getLogger(__name__)


class MediaIdStates(StatesGroup):
    waiting_for_media = State()


class MediaIdHelperFeature(BaseFeature):
    """Команда /code_id: показать file_id медиа в личке (для рассылок и интеграций)."""

    name = "media_id_helper"

    def __init__(self, user_storage, bot, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager

    async def initialize(self) -> None:
        logger.info("[%s] Фича инициализирована", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.cmd_code_id, PRIVATE_CHAT, Command("code_id"))
        dp.message.register(
            self._handle_media,
            PRIVATE_CHAT,
            StateFilter(MediaIdStates.waiting_for_media),
        )

    async def cmd_code_id(self, message: Message, state: FSMContext):
        await state.set_state(MediaIdStates.waiting_for_media)
        await message.answer(
            "<b>🖼 Получение file_id</b>\n\n"
            "Отправьте мне любой медиафайл (фото, видео, голосовое, видео-кружок, аудио, документ),\n"
            "а я верну вам его <code>file_id</code>.\n\n"
            "Этот ID можно использовать в рассылках и других местах.",
            parse_mode=ParseMode.HTML,
        )

    async def _handle_media(self, message: Message, state: FSMContext):
        file_id = None
        media_type = None
        file_name = None
        duration = None

        if message.photo:
            file_id = message.photo[-1].file_id
            media_type = "photo"
            file_name = f"photo_{file_id[:8]}.jpg"
        elif message.video:
            file_id = message.video.file_id
            media_type = "video"
            duration = message.video.duration
            file_name = message.video.file_name or f"video_{file_id[:8]}.mp4"
        elif message.voice:
            file_id = message.voice.file_id
            media_type = "voice"
            duration = message.voice.duration
            file_name = f"voice_{file_id[:8]}.ogg"
        elif message.audio:
            file_id = message.audio.file_id
            media_type = "audio"
            duration = message.audio.duration
            file_name = message.audio.file_name or f"audio_{file_id[:8]}.mp3"
        elif message.video_note:
            file_id = message.video_note.file_id
            media_type = "video_note"
            duration = message.video_note.duration
            file_name = f"video_note_{file_id[:8]}.mp4"
        elif message.document:
            file_id = message.document.file_id
            media_type = "document"
            file_name = message.document.file_name or f"document_{file_id[:8]}"
        else:
            await message.answer(
                "❌ Неподдерживаемый тип файла.\n"
                "Отправьте: фото, видео, голосовое, аудио, видео-кружок или документ."
            )
            await state.clear()
            return

        response_text = (
            f"✅ <b>{media_type.upper()} получен!</b>\n\n"
            f"📎 <b>file_id:</b>\n<code>{file_id}</code>\n\n"
            f"📋 <b>Тип:</b> <code>{media_type}</code>"
        )
        if duration:
            response_text += f"\n⏱ <b>Длительность:</b> {duration} сек"
        if file_name:
            response_text += f"\n📄 <b>Имя файла:</b> <code>{file_name}</code>"

        await message.answer(response_text, parse_mode=ParseMode.HTML)
        await state.clear()
        logger.info(
            "📎 Media ID sent to user %s, type=%s",
            message.from_user.id if message.from_user else 0,
            media_type,
        )
