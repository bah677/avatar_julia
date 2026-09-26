"""Загрузка прошлых и текущих сторис эксперта в RAG."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.features.main_menu import MENU_CB, back_keyboard
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT
from bot.states import StoriesUploadStates
from course.products import active_product_id, product_display_name
from rag.material_index import format_chunk_heading, v2_base_metadata

logger = logging.getLogger(__name__)

STORIES_CB = "ss:"


class StoriesIntakeFeature(BaseFeature):
    name = "stories_intake"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.callback_query.register(
            self.on_cb, CALLBACK_PRIVATE_CHAT, F.data.startswith(STORIES_CB)
        )

    def _stor(self):
        return self._app.user_storage

    def _hub_kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Прошлые", callback_data=f"{STORIES_CB}when:archive"),
                    InlineKeyboardButton(text="Текущие", callback_data=f"{STORIES_CB}when:current"),
                ],
                [InlineKeyboardButton(text="← Меню", callback_data=f"{MENU_CB}home")],
            ]
        )

    async def show_hub(self, message: Message) -> None:
        await message.answer(
            "<b>Сторис в базу</b>\n\n"
            "Выберите, это архив (как она уже прогревала) или текущие. "
            "Дальше присылайте текст, голос, видео или фото с подписью — пачкой, как удобно. "
            "Когда закончите — «Готово».",
            parse_mode=ParseMode.HTML,
            reply_markup=self._hub_kb(),
        )

    async def on_cb(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await is_admin_or_super(self._stor(), uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        raw = (callback.data or "")[len(STORIES_CB) :]
        if raw.startswith("when:"):
            when = raw.split(":", 1)[1]
            if when not in ("archive", "current"):
                await callback.answer()
                return
            await state.set_state(StoriesUploadStates.collecting)
            await state.update_data(stories_when=when, stories_count=0)
            label = "прошлые" if when == "archive" else "текущие"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Готово", callback_data=f"{STORIES_CB}done")],
                    [InlineKeyboardButton(text="← Назад", callback_data=f"{MENU_CB}stories_up")],
                ]
            )
            await callback.message.answer(
                f"Жду {label} сторис. Можно несколько сообщений подряд.",
                reply_markup=kb,
            )
            await callback.answer()
            return
        if raw == "done":
            data = await state.get_data()
            n = int(data.get("stories_count") or 0)
            await state.clear()
            await callback.message.answer(
                f"Приняла {n} фрагментов сторис в RAG.",
                reply_markup=back_keyboard(),
            )
            await callback.answer()
            return
        await callback.answer()

    async def try_handle_upload(self, message: Message, state: FSMContext, text: str) -> bool:
        cur = await state.get_state()
        if cur != StoriesUploadStates.collecting.state:
            return False
        uid = message.from_user.id if message.from_user else 0
        if not await is_admin_or_super(self._stor(), uid):
            return False
        body = (text or "").strip()
        if not body:
            await message.answer("Нужен текст, подпись или распознанный голос — иначе в базу нечего класть.")
            return True
        data = await state.get_data()
        when = data.get("stories_when") or "current"
        n = await self._index_one(uid, body, when=when)
        await state.update_data(stories_count=int(data.get("stories_count") or 0) + (1 if n else 0))
        if n:
            await message.answer(f"В базе: {n} чанк(ов). Ещё или «Готово».")
        else:
            await message.answer("Не записала (похоже на дубль или пусто).")
        return True

    async def _index_one(self, user_id: int, text: str, *, when: str) -> int:
        rs = getattr(self._app, "rag_stack", None)
        if rs is None:
            return 0
        pid = active_product_id()
        source_id = str(uuid.uuid4())
        when_ru = "прошлые" if when == "archive" else "текущие"
        heading = format_chunk_heading(
            product_name=product_display_name(pid),
            kind="stories",
        ) + f" · {when_ru}"
        meta = v2_base_metadata(
            product_id=pid,
            source_id=source_id,
            source_kind="stories",
            origin="telegram_stories",
        )
        meta["content_category"] = "story"
        meta["stories_when"] = when
        meta["added_by"] = int(user_id or 0)
        salt = f"stories:{user_id}:{source_id}"
        try:
            n, _ = rs.materials.add_material_text(
                text,
                base_metadata=meta,
                source=source_id[:80],
                dedupe_salt=salt,
                heading=heading,
            )
        except Exception as e:
            logger.exception("stories index: %s", e)
            return 0
        return int(n or 0)
