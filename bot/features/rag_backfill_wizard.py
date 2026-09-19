"""Мастер догрузки прошлых материалов в RAG (админская ветка)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set, Tuple

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
from bot.utils.rag_admin_context import (
    is_rag_admin_message,
    rag_admin_chat_id,
    rag_admin_chat_topic,
    rag_admin_topic_id,
)
from config import config
from telemost_mail.backfill_stats import BackfillStats
from telemost_mail.service import TelemostMailService
from yandex_disk.sync import YandexDiskSyncService

logger = logging.getLogger(__name__)

CB_PREFIX = "rb:"
CB_SRC = f"{CB_PREFIX}src:"
CB_DAYS = f"{CB_PREFIX}days:"
CB_CANCEL = f"{CB_PREFIX}cancel"

_wizard: Dict[Tuple[int, int], dict[str, Any]] = {}
_active_users: Set[int] = set()


class RagBackfillFeature(BaseFeature):
    name = "rag_backfill"

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

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.message.register(
            self.cmd_rag_backfill,
            Command("rag_backfill"),
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
        )
        dispatcher.callback_query.register(
            self.on_callback,
            F.data.startswith(CB_PREFIX),
        )

    def _wizard_key(self, chat_id: int, user_id: int) -> Tuple[int, int]:
        return int(chat_id), int(user_id)

    def _source_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📧 Почта (Телемост)",
                        callback_data=f"{CB_SRC}mail",
                    ),
                    InlineKeyboardButton(
                        text="💾 Яндекс.Диск",
                        callback_data=f"{CB_SRC}disk",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=CB_CANCEL,
                    ),
                ],
            ]
        )

    def _days_keyboard(self) -> InlineKeyboardMarkup:
        rows = []
        for n in (7, 14, 30, 60, 90, 180):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{n} дн.",
                        callback_data=f"{CB_DAYS}{n}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text="Отмена", callback_data=CB_CANCEL)]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def cmd_rag_backfill(self, message: Message) -> None:
        if not is_rag_admin_message(
            message.chat.id, message.message_thread_id
        ):
            admin_chat = rag_admin_chat_id()
            admin_topic = rag_admin_topic_id()
            if admin_chat and int(message.chat.id) == admin_chat:
                await message.reply(
                    f"Команда только в админской ветке "
                    f"(топик <code>{admin_topic}</code>).",
                    parse_mode=ParseMode.HTML,
                )
            else:
                logger.info(
                    "rag_backfill: wrong chat %s thread %s (need %s/%s)",
                    message.chat.id,
                    message.message_thread_id,
                    admin_chat,
                    admin_topic,
                )
            return
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.reply("Команда только для администратора.")
            return
        if uid in _active_users:
            await message.reply(
                "У вас уже идёт догрузка. Дождитесь сводки в этой ветке."
            )
            return

        _wizard[self._wizard_key(message.chat.id, uid)] = {}
        await message.reply(
            "<b>Догрузка материалов в RAG</b>\n\n"
            "Откуда взять материалы?",
            parse_mode=ParseMode.HTML,
            reply_markup=self._source_keyboard(),
        )

    async def on_callback(self, callback: CallbackQuery) -> None:
        msg = callback.message
        if not msg or not msg.chat:
            await callback.answer()
            return
        if not is_rag_admin_message(msg.chat.id, msg.message_thread_id):
            await callback.answer("Только в админской ветке RAG.", show_alert=True)
            return

        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только для администратора.", show_alert=True)
            return

        data = (callback.data or "").strip()
        key = self._wizard_key(msg.chat.id, uid)

        if data == CB_CANCEL:
            _wizard.pop(key, None)
            await callback.answer("Отменено")
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        if data.startswith(CB_SRC):
            source = data[len(CB_SRC) :]
            if source not in ("mail", "disk"):
                await callback.answer("Неизвестный источник")
                return
            _wizard[key] = {"source": source}
            label = "почта (Телемост)" if source == "mail" else "Яндекс.Диск"
            await callback.answer()
            await msg.edit_text(
                f"<b>Догрузка RAG</b>\n"
                f"Источник: <b>{label}</b>\n\n"
                "За сколько дней назад смотреть материалы?",
                parse_mode=ParseMode.HTML,
                reply_markup=self._days_keyboard(),
            )
            return

        if data.startswith(CB_DAYS):
            if uid in _active_users:
                await callback.answer("Догрузка уже запущена", show_alert=True)
                return
            try:
                days = int(data[len(CB_DAYS) :])
            except ValueError:
                await callback.answer("Некорректное число дней")
                return
            state = _wizard.get(key) or {}
            source = state.get("source")
            if source not in ("mail", "disk"):
                await callback.answer("Сначала выберите источник", show_alert=True)
                return

            _wizard.pop(key, None)
            _active_users.add(uid)
            await callback.answer("Запускаю…")
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            src_label = "почта" if source == "mail" else "диск"
            await msg.reply(
                f"⏳ Догрузка с <b>{src_label}</b> за <b>{days}</b> дн. "
                "запущена в фоне. По каждому новому письму — кнопки, "
                "если его ещё нет в RAG.",
                parse_mode=ParseMode.HTML,
            )
            asyncio.create_task(
                self._run_backfill(uid, source, days),
                name=f"rag_backfill_{source}_{days}",
            )
            return

        await callback.answer()

    async def _run_backfill(self, user_id: int, source: str, days: int) -> None:
        try:
            if source == "mail":
                stats = await self._backfill_mail(days)
            else:
                stats = await self._backfill_disk(days)
            await self._send_summary(stats)
        except Exception as e:
            logger.exception("rag_backfill: %s", e)
            chat_id, topic_id = rag_admin_chat_topic()
            if chat_id and self._app:
                await self._app.bot.send_message(
                    chat_id,
                    f"❌ Ошибка догрузки: {e}",
                    parse_mode=ParseMode.HTML,
                    message_thread_id=topic_id,
                )
        finally:
            _active_users.discard(user_id)

    def _telemost_service(self) -> Optional[TelemostMailService]:
        if self._app is None:
            return None
        rs = getattr(self._app, "rag_stack", None)
        return TelemostMailService.from_config(
            config,
            user_storage=self._app.user_storage,
            material_index=rs.materials if rs else None,
        )

    def _disk_service(self) -> Optional[YandexDiskSyncService]:
        if self._app is None:
            return None
        rs = getattr(self._app, "rag_stack", None)
        return YandexDiskSyncService.from_config(
            config,
            user_storage=self._app.user_storage,
            openai_client=getattr(self._app, "openai_client", None),
            material_index=rs.materials if rs else None,
            bot_app=self._app,
        )

    async def _backfill_mail(self, days: int) -> BackfillStats:
        svc = self._telemost_service()
        if svc is None or not svc.enabled:
            stats = BackfillStats(source="mail", days=days)
            stats.messages.append("Почта Телемост не настроена")
            return stats

        tm_feature = None
        if self._app and self._app.feature_manager:
            tm_feature = self._app.feature_manager.get_optional("telemost_mail")
        bot = self._app.bot if self._app else None

        async def notify_cb(note: dict) -> None:
            if bot and tm_feature:
                await tm_feature.notify_pending_note(bot, note)

        return await svc.backfill_mail(days, notify_cb=notify_cb)

    async def _backfill_disk(self, days: int) -> BackfillStats:
        svc = self._disk_service()
        if svc is None or not svc.enabled:
            stats = BackfillStats(source="disk", days=days)
            stats.messages.append("Синхронизация Я.Диска не настроена")
            return stats
        return await svc.backfill_disk(days)

    async def _send_summary(self, stats: BackfillStats) -> None:
        chat_id, topic_id = rag_admin_chat_topic()
        if not chat_id or not self._app:
            return
        await self._app.bot.send_message(
            chat_id,
            stats.summary_html(),
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )
