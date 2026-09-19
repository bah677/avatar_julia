"""Команда /style: паспорт голоса, пересборка, загрузка своей версии."""

from __future__ import annotations

from typing import Any

from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT, PRIVATE_CHAT
from bot.states import StyleUploadStates
from course.llm import CourseLLM
from course.products import active_product, active_product_id
from course.style import build_style_passport, source_hash


class StyleFeature(BaseFeature):
    name = "style_feature"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.callback_query.register(
            self.on_cb, CALLBACK_PRIVATE_CHAT, lambda c: (c.data or "").startswith("st:")
        )
        dispatcher.message.register(
            self.on_upload, PRIVATE_CHAT, StyleUploadStates.waiting_file
        )

    def _stor(self):
        return self._app.user_storage

    async def cmd_style(self, message: Message) -> None:
        await self.show_style(message, user_id=message.from_user.id)

    async def show_style(self, message: Message, *, user_id: int) -> None:
        if not await is_admin_or_super(self._stor(), user_id):
            return
        pid = active_product_id()
        row = await self._stor().get_active_style_profile(pid)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Пересобрать", callback_data="st:rebuild"),
            InlineKeyboardButton(text="📤 Загрузить свою версию", callback_data="st:up"),
        ]])
        if not row:
            await message.answer(
                f"Паспорт голоса для {active_product().name} ещё не собран.",
                reply_markup=kb,
            )
            return
        text = row.get("text") or ""
        await message.answer_document(
            BufferedInputFile(text.encode("utf-8"), filename=f"style_{pid}_v{row.get('version')}.md"),
            caption=f"Паспорт голоса · {active_product().name} · v{row.get('version')} ({row.get('origin')})",
            reply_markup=kb,
        )

    async def on_cb(self, callback: CallbackQuery, state: FSMContext) -> None:
        action = (callback.data or "").split(":")[1]
        if action == "up":
            await state.set_state(StyleUploadStates.waiting_file)
            await callback.message.answer("Пришлите файл .md / .txt с паспортом голоса.")
            await callback.answer()
            return
        if action == "rebuild":
            await callback.answer("Собираю…")
            worker = getattr(self._app, "course_worker", None)
            if worker:
                await worker._rebuild_style(active_product_id())
            await callback.message.answer("Паспорт пересобран (если не зафиксирована ручная версия).")
            return
        await callback.answer()

    async def on_upload(self, message: Message, state: FSMContext) -> None:
        text = ""
        if message.document:
            file = await message.bot.download(message.document)
            raw = file.read() if hasattr(file, "read") else file.getvalue()
            text = raw.decode("utf-8", errors="replace")
        elif message.text:
            text = message.text
        if not text.strip():
            await message.answer("Пустой файл.")
            return
        await self._stor().insert_style_profile(
            product_id=active_product_id(),
            text=text,
            origin="manual",
            created_by=message.from_user.id,
            activate=True,
        )
        await state.clear()
        await message.answer("Сохранил как активную версию. Автопересборка её не перезапишет.")

    async def is_waiting(self, state: FSMContext) -> bool:
        cur = await state.get_state()
        return cur == StyleUploadStates.waiting_file.state
