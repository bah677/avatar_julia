"""
Логирование всех действий пользователя.
"""

from typing import Any, Dict, Optional

from storage.user_storage import UserStorage


class InteractionLogger:
    """Логирует все взаимодействия пользователя с ботом."""

    def __init__(self, user_storage: UserStorage):
        self.user_storage = user_storage

    async def log(
        self,
        user_id: int,
        event_category: str,
        event_type: str,
        processing_time_ms: Optional[int] = None,
        message_id: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
        *,
        update_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        chat_type: Optional[str] = None,
        telegram_message_id: Optional[int] = None,
        callback_data: Optional[str] = None,
        command: Optional[str] = None,
        source: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> bool:
        """
        Пишет запись в ``interaction_logs`` (в т. ч. топ-полями для фильтрации —
        см. ``migrations/005_interaction_logs_enrich.sql``).
        """
        return await self.user_storage.log_interaction(
            user_id=user_id,
            event_category=event_category,
            event_type=event_type,
            processing_time_ms=processing_time_ms,
            message_id=message_id,
            data=data,
            update_id=update_id,
            chat_id=chat_id,
            chat_type=chat_type,
            telegram_message_id=telegram_message_id,
            callback_data=callback_data,
            command=command,
            source=source,
            outcome=outcome,
        )
