"""Живые Telegram-чаты для индексации переписки в RAG."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RagIndexChatsMixin:
    async def upsert_rag_index_chat(
        self,
        *,
        chat_id: int,
        title: str = "",
        added_by: Optional[int] = None,
        enabled: bool = True,
    ) -> bool:
        """True, если строка новая (или была выключена и снова включена)."""
        prev = await self.get_rag_index_chat(chat_id)
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO rag_index_chats (chat_id, title, added_by, enabled, updated_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = CASE
                        WHEN EXCLUDED.title <> '' THEN EXCLUDED.title
                        ELSE rag_index_chats.title
                    END,
                    added_by = COALESCE(EXCLUDED.added_by, rag_index_chats.added_by),
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                """,
                int(chat_id),
                (title or "").strip()[:500],
                added_by,
                enabled,
            )
        is_new = prev is None or not bool(prev.get("enabled"))
        logger.info(
            "rag_index_chats: chat_id=%s new=%s enabled=%s title=%r",
            chat_id,
            is_new,
            enabled,
            title,
        )
        return is_new and enabled

    async def disable_rag_index_chat(self, chat_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE rag_index_chats
                   SET enabled = FALSE, updated_at = NOW()
                 WHERE chat_id = $1
                """,
                int(chat_id),
            )

    async def list_enabled_rag_index_chat_ids(self) -> List[int]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                "SELECT chat_id FROM rag_index_chats WHERE enabled = TRUE"
            )
        return [int(r["chat_id"]) for r in rows]

    async def get_rag_index_chat(self, chat_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM rag_index_chats WHERE chat_id = $1",
                int(chat_id),
            )
        return dict(row) if row else None
