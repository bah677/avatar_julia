"""
Публичные / приватные ссылки на источники RAG.

На чанке заполняется ровно одно поле: ``public_source_link`` или ``private_source_link``.
"""

from __future__ import annotations

import base64
import logging
import uuid
from html import escape as html_escape
from typing import Any, Dict, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.features.base import BaseFeature
from bot.utils.rag_admin_context import rag_admin_chat_topic
from storage.db.rag_source_visibility import VIS_PRIVATE, VIS_PUBLIC

logger = logging.getLogger(__name__)

SOURCE_TELEGRAM_GROUP = "telegram_group"
SOURCE_YANDEX_DISK_FOLDER = "yandex_disk_folder"

CB_PREFIX = "rag_src:"


def _encode_key(source_type: str, source_key: str) -> str:
    raw = f"{source_type}|{source_key}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_key(token: str) -> tuple[str, str]:
    pad = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(token + pad).decode("utf-8")
    st, sk = raw.split("|", 1)
    return st, sk


def apply_source_link_to_metadata(
    meta: Dict[str, Any],
    url: str,
    visibility: Optional[str],
) -> None:
    """Записать ссылку в public или private поле; legacy ``group_message_link`` убрать."""
    meta.pop("group_message_link", None)
    meta.pop("public_source_link", None)
    meta.pop("private_source_link", None)
    link = (url or "").strip()
    if not link or not visibility:
        return
    if visibility == VIS_PUBLIC:
        meta["public_source_link"] = link[:500]
    elif visibility == VIS_PRIVATE:
        meta["private_source_link"] = link[:500]


def youtube_public_link(url: str) -> Dict[str, str]:
    """YouTube всегда публичный — без запроса админу."""
    u = (url or "").strip()
    if not u:
        return {}
    return {"public_source_link": u[:500]}


def _notify_chat_topic() -> tuple[int, Optional[int]]:
    return rag_admin_chat_topic()


async def resolve_source_visibility(
    app: Any,
    *,
    source_type: str,
    source_key: str,
    label: str,
) -> Optional[str]:
    """
  Возвращает ``public`` / ``private`` или ``None`` (ещё не решено — вопрос в топике).
    """
    if app is None:
        return None
    storage = app.user_storage
    vis = await storage.get_rag_source_visibility(source_type, source_key)
    if vis:
        return vis

    pending = await storage.get_or_create_rag_source_pending(
        source_type=source_type,
        source_key=source_key,
        label=label,
    )
    if not pending or pending.get("notify_sent"):
        return None

    chat_id, topic_id = _notify_chat_topic()
    if not chat_id:
        logger.warning(
            "rag_source_visibility: нет RAG_ADMIN_CHAT_ID — ссылка не будет на чанке"
        )
        await storage.mark_rag_source_pending_notified(pending["id"])
        return None

    token = _encode_key(source_type, source_key)
    type_label = {
        SOURCE_TELEGRAM_GROUP: "группа Telegram",
        SOURCE_YANDEX_DISK_FOLDER: "папка Яндекс.Диска",
    }.get(source_type, source_type)

    text = (
        f"🔗 <b>Новый источник RAG</b>\n"
        f"Тип: <b>{html_escape(type_label)}</b>\n"
        f"<code>{html_escape((label or source_key)[:400])}</code>\n\n"
        f"Куда относить ссылки на материалы из этого источника?"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Публичный",
                    callback_data=f"{CB_PREFIX}{token}:pub",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="Приватный",
                    callback_data=f"{CB_PREFIX}{token}:priv",
                    style="danger",
                ),
            ]
        ]
    )
    try:
        await app.bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            message_thread_id=topic_id,
        )
        await storage.mark_rag_source_pending_notified(pending["id"])
    except Exception as e:
        logger.error("rag_source_visibility notify: %s", e)
    return None


class RagSourceVisibilityFeature(BaseFeature):
    name = "rag_source_visibility"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.callback_query.register(
            self.on_callback,
            F.data.startswith(CB_PREFIX),
        )

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    async def on_callback(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await self._is_admin(uid):
            await callback.answer("Только для администратора.", show_alert=True)
            return

        data = (callback.data or "").strip()
        if not data.startswith(CB_PREFIX):
            await callback.answer()
            return
        rest = data[len(CB_PREFIX) :]
        if rest.endswith(":pub"):
            vis = VIS_PUBLIC
            token = rest[:-4]
        elif rest.endswith(":priv"):
            vis = VIS_PRIVATE
            token = rest[:-5]
        else:
            await callback.answer("Некорректные данные", show_alert=True)
            return

        try:
            source_type, source_key = _decode_key(token)
        except Exception:
            await callback.answer("Ошибка разбора источника", show_alert=True)
            return

        label = source_key
        pending = await self._app.user_storage.get_or_create_rag_source_pending(
            source_type=source_type,
            source_key=source_key,
            label=label,
        )
        if pending and pending.get("label"):
            label = pending["label"]

        ok = await self._app.user_storage.set_rag_source_visibility(
            source_type=source_type,
            source_key=source_key,
            visibility=vis,
            label=label,
            decided_by=uid,
        )
        if not ok:
            await callback.answer("Не удалось сохранить", show_alert=True)
            return

        vis_ru = "публичный" if vis == VIS_PUBLIC else "приватный"
        msg = callback.message
        if msg:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            try:
                await msg.reply(
                    f"✓ Источник помечен как <b>{vis_ru}</b>. "
                    f"Новые материалы получат ссылку в поле "
                    f"<code>{'public' if vis == VIS_PUBLIC else 'private'}_source_link</code>.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        await callback.answer(f"Сохранено: {vis_ru}")
