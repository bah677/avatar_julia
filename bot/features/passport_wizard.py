"""Живой мастер паспортов эксперта / продукта / запуска."""

from __future__ import annotations

import logging
from typing import Any, Dict

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.features.main_menu import MENU_CB, back_keyboard
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT
from bot.states import PassportTalkStates
from config import config
from course.llm import CourseLLM
from course.passports import (
    PASSPORT_KINDS,
    build_turn_system,
    filled_count,
    load_passport,
    merge_slots,
    save_passport,
    spec_for,
)
from course.products import active_product, active_product_id, product_display_name

logger = logging.getLogger(__name__)

PASSPORT_CB = "pp:"


def _kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сохранить как есть", callback_data=f"{PASSPORT_CB}save")],
            [InlineKeyboardButton(text="← Паспорта", callback_data=f"{MENU_CB}passports")],
        ]
    )


class PassportWizardFeature(BaseFeature):
    name = "passport_wizard"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.callback_query.register(
            self.on_cb, CALLBACK_PRIVATE_CHAT, F.data.startswith(PASSPORT_CB)
        )

    def _stor(self):
        return self._app.user_storage

    async def show_hub(self, message: Message, *, edit: bool = False) -> None:
        pid = active_product_id()
        lines = [
            f"<b>Паспорта · {product_display_name(pid)}</b>",
            "Живой разговор: бот смотрит RAG и спрашивает только дыры, не анкету.",
            "",
        ]
        rows = []
        for kind in PASSPORT_KINDS:
            data = await load_passport(self._stor(), kind)
            n, total = filled_count(data["slots"])
            mark = "✅" if n == total and total else ("•" if n else "○")
            title = spec_for(kind)["title"]
            lines.append(f"{mark} {title}: {n}/{total}")
            rows.append(
                [InlineKeyboardButton(text=f"Собрать: {title}", callback_data=f"{PASSPORT_CB}go:{kind}")]
            )
            if data.get("text"):
                rows.append(
                    [InlineKeyboardButton(text=f"Показать {title}", callback_data=f"{PASSPORT_CB}show:{kind}")]
                )
        rows.append([InlineKeyboardButton(text="← Меню", callback_data=f"{MENU_CB}home")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        text = "\n".join(lines)
        if edit:
            try:
                await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def on_cb(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await is_admin_or_super(self._stor(), uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        raw = (callback.data or "")[len(PASSPORT_CB) :]
        msg = callback.message
        if raw == "save":
            data = await state.get_data()
            kind = data.get("passport_kind")
            slots = data.get("passport_slots") or {}
            if kind:
                await save_passport(self._stor(), kind, slots, done=True, user_id=uid)
                await state.clear()
                await callback.message.answer("Сохранила паспорт.", reply_markup=back_keyboard())
            await callback.answer()
            return
        if raw.startswith("show:"):
            kind = raw.split(":", 1)[1]
            data = await load_passport(self._stor(), kind)
            text = (data.get("text") or "Пока пусто.").strip()
            if len(text) > 3500:
                text = text[:3490] + "…"
            await callback.message.answer(text, reply_markup=_kb_home())
            await callback.answer()
            return
        if raw.startswith("go:"):
            kind = raw.split(":", 1)[1]
            await callback.answer()
            if msg:
                await self.start_talk(msg, state, kind=kind, user_id=uid)
            return
        await callback.answer()

    async def start_talk(
        self, message: Message, state: FSMContext, *, kind: str, user_id: int
    ) -> None:
        saved = await load_passport(self._stor(), kind)
        await state.set_state(PassportTalkStates.talking)
        await state.update_data(passport_kind=kind, passport_slots=saved["slots"])
        opener = (
            f"<b>{spec_for(kind)['title']}</b> · {active_product().name}\n"
            "Говорите свободно — голосом или текстом. Я сверюсь с базой и спрошу только пробелы."
        )
        await message.answer(opener, parse_mode=ParseMode.HTML, reply_markup=_kb_home())
        reply = await self._llm_turn(user_id, kind, saved["slots"], user_text="")
        if reply:
            await message.answer(reply, reply_markup=_kb_home())

    async def try_handle_text(self, message: Message, state: FSMContext, text: str) -> bool:
        cur = await state.get_state()
        if cur != PassportTalkStates.talking.state:
            return False
        uid = message.from_user.id if message.from_user else 0
        if not await is_admin_or_super(self._stor(), uid):
            return False
        data = await state.get_data()
        kind = data.get("passport_kind") or "expert"
        slots = data.get("passport_slots") or {}
        reply, slots, done = await self._llm_turn_full(uid, kind, slots, text or "")
        await state.update_data(passport_slots=slots)
        if reply:
            await message.answer(reply, reply_markup=_kb_home())
        if done:
            await save_passport(self._stor(), kind, slots, done=True, user_id=uid)
            await state.clear()
            await message.answer("Паспорт сохранён.", reply_markup=back_keyboard())
        return True

    async def _rag_digest(self, kind: str) -> str:
        rs = getattr(self._app, "rag_stack", None)
        if rs is None:
            return ""
        q = spec_for(kind).get("rag_query") or spec_for(kind)["title"]
        try:
            return await rs.retriever.retrieve_context_async(q, top_k=5)
        except Exception as e:
            logger.warning("passport rag: %s", e)
            return ""

    async def _llm_turn(self, user_id: int, kind: str, slots: Dict[str, str], user_text: str) -> str:
        reply, _, _ = await self._llm_turn_full(user_id, kind, slots, user_text)
        return reply

    async def _llm_turn_full(
        self, user_id: int, kind: str, slots: Dict[str, str], user_text: str
    ) -> tuple[str, Dict[str, str], bool]:
        digest = await self._rag_digest(kind)
        llm = CourseLLM(self._stor())
        model = getattr(config, "COURSE_MINING_MODEL", "gpt-4o-mini")
        user = (user_text or "").strip() or "(эксперт ещё ничего не сказал — начни с самого дырявого слота, без анкеты)"
        try:
            data = await llm.complete_json(
                model=model,
                messages=[
                    {"role": "system", "content": build_turn_system(kind, slots, digest)},
                    {"role": "user", "content": user},
                ],
                user_id=user_id,
                temperature=0.4,
                max_tokens=1200,
                request_kind="passport_wizard",
            )
        except Exception as e:
            logger.exception("passport turn: %s", e)
            return "Не смогла сходить к модели. Напишите ещё раз или сохраните как есть.", slots, False
        slots = merge_slots(kind, slots, data.get("slots") if isinstance(data.get("slots"), dict) else {})
        reply = str(data.get("reply") or "").strip() or "Продолжайте — я слушаю."
        done = bool(data.get("done"))
        return reply, slots, done
