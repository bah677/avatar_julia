"""Сессия creative RAG-задачи и ходы диалога в рамках task_id."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Состояния creative_sessions.state
CS_IDLE = "idle"
CS_CONFIRM_NEW = "confirm_new"
CS_PICK_CONTENT_TYPE = "pick_content_type"
CS_PICK_PRODUCT = "pick_product"
CS_AWAITING_CUSTOM_CONTENT_TYPE = "awaiting_custom_content_type"
CS_AWAITING_CUSTOM_PRODUCT = "awaiting_custom_product"
CS_AWAITING_TOPIC = "awaiting_topic"
CS_ACTIVE = "active"


class CreativeSessionsMixin:
    async def get_creative_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, state, product, content_type, topic, task_id, updated_at
                FROM creative_sessions
                WHERE user_id = $1
                """,
                user_id,
            )
        if not row:
            return None
        return dict(row)

    async def upsert_creative_session(
        self,
        user_id: int,
        *,
        state: str,
        product: Optional[str] = None,
        content_type: Optional[str] = None,
        topic: Optional[str] = None,
        task_id: Optional[Any] = None,
    ) -> None:
        tid = task_id
        if tid is not None and not isinstance(tid, uuid.UUID):
            try:
                tid = uuid.UUID(str(tid))
            except ValueError:
                tid = None

        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO creative_sessions (
                    user_id, state, product, content_type, topic, task_id, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    product = EXCLUDED.product,
                    content_type = EXCLUDED.content_type,
                    topic = EXCLUDED.topic,
                    task_id = EXCLUDED.task_id,
                    updated_at = NOW()
                """,
                user_id,
                state,
                product,
                content_type,
                topic,
                tid,
            )

    async def reset_creative_session_row(self, user_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO creative_sessions (
                    user_id, state, product, content_type, topic, task_id, updated_at
                )
                VALUES ($1, 'idle', NULL, NULL, NULL, NULL, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    state = 'idle',
                    product = NULL,
                    content_type = NULL,
                    topic = NULL,
                    task_id = NULL,
                    updated_at = NOW()
                """,
                user_id,
            )

    async def append_creative_task_turn(
        self,
        user_id: int,
        task_id: Any,
        role: str,
        content: str,
    ) -> None:
        tid = task_id
        if not isinstance(tid, uuid.UUID):
            tid = uuid.UUID(str(tid))
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO creative_task_turns (user_id, task_id, role, content)
                VALUES ($1, $2, $3, $4)
                """,
                user_id,
                tid,
                role,
                (content or "")[:24000],
            )

    async def get_creative_task_turns(
        self,
        task_id: Any,
        *,
        max_messages: int = 24,
    ) -> List[Dict[str, str]]:
        """Хронологический порядок: user/assistant для подстановки в LLM."""
        tid = task_id
        if not isinstance(tid, uuid.UUID):
            tid = uuid.UUID(str(tid))
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content
                FROM creative_task_turns
                WHERE task_id = $1
                ORDER BY id DESC
                LIMIT $2
                """,
                tid,
                max_messages,
            )
        chrono = list(reversed(rows))
        return [{"role": r["role"], "content": r["content"]} for r in chrono]
