"""Единственная команда /menu: главное меню и инлайн-кнопки."""

from __future__ import annotations

from html import escape as html_escape
from typing import Any

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_admin_or_super, is_super_admin_user_id
from bot.features.base import BaseFeature
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT, PRIVATE_CHAT
from bot.states import MenuFocusStates
from config import config
from course.products import active_product, active_product_id

MENU_CB = "mn:"

HELP_TEXT = (
    "<b>Как работать</b>\n\n"
    "Бот — редакция: сам забирает материалы активного продукта, один раз "
    "разбирает их в карточки идей и пишет черновики в вашем голосе.\n\n"
    "<b>Меню</b> — команда <code>/menu</code>. Дальше только кнопки.\n\n"
    "<b>Материалы</b>\n"
    "• На Яндекс.Диск: папка <code>00 Эксперт/</code> и папка продукта "
    "(уроки, конспекты, слайды, практики, эфиры, посты).\n"
    "• Ссылку на видео (YouTube, Vimeo) пришлите в эту личку.\n\n"
    "<b>Черновики</b>\n"
    "Напишите задачу обычным текстом или голосом — сразу черновик. "
    "Ответ на черновик — правка. 👍 сохраняет удачный текст в золотой фонд, "
    "👎 — чтобы уточнить, что не так.\n\n"
    "<b>План</b> — кнопка «План недели»: состав на дни, можно отметить лишнее "
    "и написать выбранные.\n"
    "<b>Фокус</b> — о чём писать на этой неделе.\n"
    "<b>Уроки / Банк / Голос</b> — паспорт урока, карточки идей, паспорт голоса.\n"
    "<b>Очередь / Синхронизация</b> — обработка файлов и опрос Диска."
)


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"{MENU_CB}{action}")


def menu_keyboard(*, superadmin: bool) -> InlineKeyboardMarkup:
    rows = [
        [_btn("📅 План недели", "plan"), _btn("🎯 Фокус", "focus")],
        [_btn("📖 Уроки", "lessons"), _btn("💡 Банк идей", "bank")],
        [_btn("🎤 Голос", "style"), _btn("⏳ Очередь", "queue")],
        [_btn("☁️ Синхронизация Диска", "sync")],
        [_btn("✨ Новая задача", "new")],
        [_btn("❓ Как работать", "help")],
    ]
    if superadmin:
        rows.append([_btn("💸 Расходы", "costs"), _btn("🧹 Сводка RAG", "summary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("← Меню", "home")]]
    )


def menu_caption(bot_label: str) -> str:
    name = html_escape((bot_label or "").strip()) or "аватар"
    product_name = "—"
    try:
        if getattr(config, "COURSE_ENABLED", False):
            product_name = html_escape(active_product().name)
    except Exception:
        product_name = html_escape(getattr(config, "ACTIVE_PRODUCT", "") or "—")
    return (
        f"<b>{name}</b>\n"
        f"Активный продукт: {product_name}\n\n"
        "Выберите действие:"
    )


class MainMenuFeature(BaseFeature):
    name = "main_menu"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.message.register(self.cmd_menu, PRIVATE_CHAT, Command("menu"))
        dispatcher.callback_query.register(
            self.on_menu, CALLBACK_PRIVATE_CHAT, F.data.startswith(MENU_CB)
        )

    def _stor(self):
        return self._app.user_storage

    async def cmd_menu(self, message: Message, state: FSMContext) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_admin_or_super(self._stor(), uid):
            return
        await state.clear()
        await self.send_menu(message)

    async def send_menu(self, message: Message, *, edit: bool = False) -> None:
        from bot.utils.telegram_identity import resolve_telegram_bot_display_name

        uid = message.from_user.id if message.from_user else 0
        label = await resolve_telegram_bot_display_name(message.bot)
        text = menu_caption(label)
        kb = menu_keyboard(superadmin=is_super_admin_user_id(uid))
        if edit:
            try:
                await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def send_menu_to(self, *, bot, chat_id: int, user_id: int) -> None:
        from bot.utils.telegram_identity import resolve_telegram_bot_display_name

        label = await resolve_telegram_bot_display_name(bot)
        await bot.send_message(
            chat_id,
            menu_caption(label),
            parse_mode=ParseMode.HTML,
            reply_markup=menu_keyboard(superadmin=is_super_admin_user_id(user_id)),
        )

    async def on_menu(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await is_admin_or_super(self._stor(), uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        action = (callback.data or "")[len(MENU_CB) :]
        msg = callback.message
        if action == "home":
            if msg:
                from bot.utils.telegram_identity import resolve_telegram_bot_display_name

                label = await resolve_telegram_bot_display_name(msg.bot)
                try:
                    await msg.edit_text(
                        menu_caption(label),
                        parse_mode=ParseMode.HTML,
                        reply_markup=menu_keyboard(superadmin=is_super_admin_user_id(uid)),
                    )
                except Exception:
                    await msg.answer(
                        menu_caption(label),
                        parse_mode=ParseMode.HTML,
                        reply_markup=menu_keyboard(superadmin=is_super_admin_user_id(uid)),
                    )
            await callback.answer()
            return
        if action == "help":
            if msg:
                await msg.edit_text(
                    HELP_TEXT, parse_mode=ParseMode.HTML, reply_markup=back_keyboard()
                )
            await callback.answer()
            return
        await callback.answer()
        if msg is None:
            return
        studio = self._app.feature_manager.get_optional("content_studio")
        if action == "plan":
            if studio:
                await msg.answer("Собираю план…")
                await studio.build_and_send_plan(msg.chat.id, uid)
            return
        if action == "focus":
            await self._show_focus(msg, uid, state)
            return
        if action == "focus_off":
            await self._stor().set_content_setting(active_product_id(), "focus", "")
            await msg.answer("Фокус сброшен.", reply_markup=back_keyboard())
            return
        if action == "lessons":
            studio = self._app.feature_manager.get_optional("content_studio")
            if studio:
                await studio.show_lessons(msg.chat.id, uid, bot=msg.bot, reply_markup=back_keyboard())
            return
        if action.startswith("les:"):
            key = action.split(":", 1)[1]
            studio = self._app.feature_manager.get_optional("content_studio")
            if studio:
                await studio.show_lesson(msg.chat.id, uid, key)
            return
        if action == "bank":
            studio = self._app.feature_manager.get_optional("content_studio")
            if studio:
                await studio.show_bank(msg.chat.id, uid)
            return
        if action == "style":
            style_f = self._app.feature_manager.get_optional("style_feature")
            if style_f:
                await style_f.show_style(msg, user_id=uid)
            return
        if action == "queue":
            intake = self._app.feature_manager.get_optional("course_intake")
            if intake:
                await intake.show_queue(msg, user_id=uid)
            return
        if action == "sync":
            disk = self._app.feature_manager.get_optional("course_disk_sync")
            if disk:
                await disk.run_sync_for_user(msg, user_id=uid)
            return
        if action == "new":
            messaging = self._app.feature_manager.get_optional("messaging")
            coord = getattr(messaging, "creative_coord", None) if messaging else None
            if coord:
                await coord.on_command_new(msg, actor=callback.from_user)
            return
        if action == "costs":
            if not is_super_admin_user_id(uid):
                return
            studio = self._app.feature_manager.get_optional("content_studio")
            if studio:
                await studio.show_costs(msg, days=7)
            return
        if action == "summary":
            if not is_super_admin_user_id(uid):
                return
            await self._rag_summary(msg)
            return

    async def _show_focus(self, msg: Message, uid: int, state: FSMContext) -> None:
        val = await self._stor().get_content_setting(active_product_id(), "focus")
        current = val if isinstance(val, str) else (str(val) if val else "")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [_btn("Сбросить", "focus_off")],
                [_btn("← Меню", "home")],
            ]
        )
        await msg.answer(
            f"Текущий фокус: {html_escape(current) if current else 'не задан'}\n\n"
            "Напишите новый фокус следующим сообщением.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        await state.set_state(MenuFocusStates.waiting_text)

    async def try_handle_focus_text(self, message: Message, state: FSMContext, text: str) -> bool:
        uid = message.from_user.id if message.from_user else 0
        if not await is_admin_or_super(self._stor(), uid):
            return False
        await self._stor().set_content_setting(active_product_id(), "focus", (text or "").strip())
        await state.clear()
        await message.answer(f"Фокус: {html_escape((text or '').strip())}", parse_mode=ParseMode.HTML)
        await self.send_menu(message)
        return True

    async def _rag_summary(self, msg: Message) -> None:
        import asyncio
        from rag.expert_stats import compute_expert_materials_statistics, format_expert_stats_html

        rs = getattr(self._app, "rag_stack", None)
        if rs is None:
            await msg.answer("База RAG не поднята.", reply_markup=back_keyboard())
            return
        try:
            stats = await asyncio.to_thread(compute_expert_materials_statistics, rs.vectors)
        except Exception as e:
            await msg.answer(f"Не удалось прочитать Chroma: {html_escape(str(e))}")
            return
        golden_n = -1
        try:
            golden_n = int(rs.vectors.golden_collection.count())
        except Exception:
            pass
        text = format_expert_stats_html(stats, golden_count=golden_n)
        if len(text) > 4000:
            text = text[:3900] + "\n\n<i>… обрезано.</i>"
        await msg.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
