"""Извлечение полей для interaction_logs из апдейтов Telegram."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from aiogram.types import CallbackQuery, Message, MessageReactionUpdated

LogDimensions = Tuple[
    Optional[int],
    Optional[int],
    Optional[str],
    Optional[int],
    Optional[str],
    Optional[str],
]


def _chat_type_str(chat) -> Optional[str]:
    if chat is None:
        return None
    t = getattr(chat, "type", None)
    if t is None:
        return None
    return t.value if hasattr(t, "value") else str(t)


def dimensions_for_interaction_logs(
    update_id: Optional[int],
    event_type: str,
    event_obj: Any,
) -> LogDimensions:
    """
    (update_id, chat_id, chat_type, telegram_message_id, command, callback_data).

    ``event_type`` — внутренний тип пайплайна: message | callback | edited_message | reaction.
    """
    command: Optional[str] = None
    callback_data: Optional[str] = None

    if isinstance(event_obj, Message):
        if event_obj.text and event_obj.text.startswith("/"):
            command = event_obj.text.split()[0].strip()
        ch = event_obj.chat
        return (
            update_id,
            ch.id if ch else None,
            _chat_type_str(ch),
            event_obj.message_id,
            command,
            callback_data,
        )

    if isinstance(event_obj, CallbackQuery):
        callback_data = event_obj.data
        msg = event_obj.message
        if msg and msg.chat:
            ch = msg.chat
            return (
                update_id,
                ch.id,
                _chat_type_str(ch),
                msg.message_id,
                None,
                callback_data,
            )
        return (update_id, None, None, None, None, callback_data)

    if isinstance(event_obj, MessageReactionUpdated):
        ch = event_obj.chat
        return (
            update_id,
            ch.id if ch else None,
            _chat_type_str(ch),
            event_obj.message_id,
            None,
            None,
        )

    return update_id, None, None, None, None, None
