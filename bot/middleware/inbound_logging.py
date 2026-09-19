"""Логирование входящего апдейта в ``messages`` + ``interaction_logs`` до доступа."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, Update

from bot.access.policies import parse_event
from bot.logging.interaction_logger import InteractionLogger
from bot.logging.message_copier import MessageCopier
from bot.logging.update_context import dimensions_for_interaction_logs

logger = logging.getLogger(__name__)


SOURCE_INBOUND = "inbound"


def _inbound_target_is_private(event_type: str, event_obj) -> bool:
    """Логируем в БД только личку; группы и каналы пропускаем без записи."""
    if event_type in ("message", "edited_message"):
        return isinstance(event_obj, Message) and (
            event_obj.chat.type == ChatType.PRIVATE
        )
    if event_type == "callback":
        if not isinstance(event_obj, CallbackQuery):
            return False
        m = event_obj.message
        return m is not None and m.chat.type == ChatType.PRIVATE
    if event_type == "reaction":
        ch = getattr(event_obj, "chat", None)
        return ch is not None and ch.type == ChatType.PRIVATE
    return True


class InboundLoggingMiddleware(BaseMiddleware):
    def __init__(
        self,
        message_copier: MessageCopier,
        interaction_logger: InteractionLogger,
    ):
        super().__init__()
        self.message_copier = message_copier
        self.interaction_logger = interaction_logger

    async def _log_event(
        self,
        event: Update,
        event_type: str,
        event_obj,
        user_id: int,
    ) -> Optional[int]:
        ud, cid, ctype, tg_mid, cmd, cb_data = dimensions_for_interaction_logs(
            event.update_id,
            event_type,
            event_obj,
        )

        try:
            if event_type == "message":
                message_id = await self.message_copier.save_incoming(event_obj)
                await self.interaction_logger.log(
                    user_id=user_id,
                    event_category="message",
                    event_type="received",
                    message_id=message_id,
                    data={
                        "text": event_obj.text or "",
                        "has_media": bool(
                            event_obj.voice
                            or event_obj.photo
                            or event_obj.video
                            or event_obj.document
                        ),
                    },
                    update_id=ud,
                    chat_id=cid,
                    chat_type=ctype,
                    telegram_message_id=tg_mid,
                    callback_data=None,
                    command=cmd,
                    source=SOURCE_INBOUND,
                    outcome="logged",
                )
                return message_id

            if event_type == "callback":
                message_id = await self.message_copier.save_callback(
                    event_obj, user_id, event_obj.data
                )
                await self.interaction_logger.log(
                    user_id=user_id,
                    event_category="callback",
                    event_type=event_obj.data or "callback",
                    message_id=message_id,
                    data={"callback_data": event_obj.data},
                    update_id=ud,
                    chat_id=cid,
                    chat_type=ctype,
                    telegram_message_id=tg_mid,
                    callback_data=cb_data,
                    command=None,
                    source=SOURCE_INBOUND,
                    outcome="logged",
                )
                return message_id

            if event_type == "edited_message":
                message_id = await self.message_copier.save_edited_message(
                    event_obj, user_id
                )
                await self.interaction_logger.log(
                    user_id=user_id,
                    event_category="message",
                    event_type="edited",
                    message_id=message_id,
                    data={"message_id": event_obj.message_id},
                    update_id=ud,
                    chat_id=cid,
                    chat_type=ctype,
                    telegram_message_id=tg_mid,
                    callback_data=None,
                    command=cmd,
                    source=SOURCE_INBOUND,
                    outcome="logged",
                )
                return message_id

            if event_type == "reaction":
                mr = event_obj
                await self.interaction_logger.log(
                    user_id=user_id,
                    event_category="reaction",
                    event_type="reaction",
                    data={
                        "message_id": mr.message_id,
                        "chat_id": mr.chat.id,
                        "new_reaction": [str(x) for x in (mr.new_reaction or [])],
                    },
                    update_id=ud,
                    chat_id=cid,
                    chat_type=ctype,
                    telegram_message_id=tg_mid,
                    callback_data=None,
                    command=None,
                    source=SOURCE_INBOUND,
                    outcome="logged",
                )
                return None

        except Exception as e:
            logger.error("Error logging inbound event: %s", e, exc_info=True)
            try:
                await self.interaction_logger.log(
                    user_id=user_id,
                    event_category="system",
                    event_type="inbound_log_error",
                    data={"error": str(e), "pipeline_event_type": event_type},
                    update_id=ud,
                    chat_id=cid,
                    chat_type=ctype,
                    telegram_message_id=tg_mid,
                    callback_data=cb_data,
                    command=cmd,
                    source=SOURCE_INBOUND,
                    outcome="error",
                )
            except Exception as log_e:
                logger.error("Failed to log inbound error: %s", log_e)

        return None

    async def __call__(self, handler, event: Update, data: Dict[str, Any]):
        event_type, event_obj, user_id = parse_event(event)

        if not event_type or user_id is None:
            return await handler(event, data)

        if not _inbound_target_is_private(event_type, event_obj):
            return await handler(event, data)

        try:
            raw = event.event_type
        except LookupError:
            raw = event_type

        data["access_event_type"] = event_type
        data["access_event_obj"] = event_obj
        data["access_user_id"] = user_id
        data["access_raw_event_type"] = raw

        logged_message_id = await self._log_event(
            event, event_type, event_obj, user_id
        )
        if logged_message_id is not None:
            data["logged_message_id"] = logged_message_id

        return await handler(event, data)
