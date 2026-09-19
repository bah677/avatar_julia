"""Кнопка принудительной нарезки видео-шортсов (на паузе, если VIDEO=0)."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.utils.rag_admin_context import is_rag_shorts_message, rag_shorts_chat_topic
from config import config
from telemost_shorts.pipeline import enqueue_telemost_shorts_last

logger = logging.getLogger(__name__)

CB_CUT_LAST = "ts:cut_last"


class TelemostShortsFeature(BaseFeature):
    name = "telemost_shorts"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def _cut_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✂️ Нарезать последний эфир (видео)",
                        callback_data=CB_CUT_LAST,
                    )
                ]
            ]
        )

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        if not getattr(config, "TELEMOST_VIDEO_SHORTS_ENABLED", False):
            self.log("TELEMOST_VIDEO_SHORTS_ENABLED=0 — видео-шортсы на паузе")
            return
        if not config.TELEMOST_SHORTS_ENABLED:
            self.log("TELEMOST_SHORTS_ENABLED=0 — хендлеры не регистрируются")
            return
        dispatcher.callback_query.register(
            self.on_cut_last_callback,
            F.data == CB_CUT_LAST,
        )
        dispatcher.message.register(
            self.cmd_shorts_panel,
            Command("shorts_cut"),
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
        )

    async def _run_cut_last(self, answer_fn) -> None:
        if self._app is None:
            await answer_fn("Бот не инициализирован", alert=True)
            return
        ok, msg = await enqueue_telemost_shorts_last(
            self._app, force=True, regenerate_moments=True
        )
        if ok:
            await answer_fn(
                f"Новая видео-нарезка: {msg[:100]}",
                alert=False,
            )
        else:
            await answer_fn(msg, alert=True)

    async def on_cut_last_callback(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только для администратора.", show_alert=True)
            return
        msg = callback.message
        if msg and not is_rag_shorts_message(msg.chat.id, msg.message_thread_id):
            await callback.answer("Кнопка только в топике шортсов.", show_alert=True)
            return

        async def answer(text: str, *, alert: bool) -> None:
            await callback.answer(text, show_alert=alert)

        await self._run_cut_last(answer)

    async def cmd_shorts_panel(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return
        if not is_rag_shorts_message(message.chat.id, message.message_thread_id):
            _, topic_id = rag_shorts_chat_topic()
            await message.reply(
                f"Команда только в топике шортсов "
                f"(ветка <code>{topic_id}</code>).",
                parse_mode=ParseMode.HTML,
            )
            return
        await message.answer(
            "🎬 Принудительная видео-нарезка:",
            reply_markup=self._cut_keyboard(),
        )
