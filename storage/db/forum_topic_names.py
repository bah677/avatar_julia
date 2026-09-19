"""Кэш названий форум-топиков: PostgreSQL (group_chat_id, topic_id, topic_name)."""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

Key = Tuple[int, int]


class ForumTopicNamesMixin:
    async def upsert_forum_topic_name(
        self,
        *,
        group_chat_id: int,
        topic_id: int,
        topic_name: str,
    ) -> None:
        n = (topic_name or "").strip()
        if not n or topic_id is None:
            return
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO forum_topic_names (group_chat_id, topic_id, topic_name, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (group_chat_id, topic_id)
                DO UPDATE SET
                    topic_name = EXCLUDED.topic_name,
                    updated_at = NOW()
                """,
                group_chat_id,
                topic_id,
                n,
            )
        logger.info(
            "forum_topic_names: upsert chat_id=%s topic_id=%s name=%r",
            group_chat_id,
            topic_id,
            n,
        )

    async def get_forum_topic_name(
        self, group_chat_id: int, topic_id: int
    ) -> Optional[str]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT topic_name FROM forum_topic_names
                WHERE group_chat_id = $1 AND topic_id = $2
                """,
                group_chat_id,
                topic_id,
            )
        if not row:
            return None
        v = (row["topic_name"] or "").strip()
        return v if v else None

    async def forum_topic_names_snapshot_for_chat(
        self, group_chat_id: int
    ) -> Dict[Key, str]:
        """Все топики чата как {(group_chat_id, topic_id): topic_name} — для отладочных логов."""
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT topic_id, topic_name FROM forum_topic_names
                WHERE group_chat_id = $1
                ORDER BY topic_id
                """,
                group_chat_id,
            )
        out: Dict[Key, str] = {}
        for r in rows:
            tid = int(r["topic_id"])
            name = (r["topic_name"] or "").strip()
            if name:
                out[(group_chat_id, tid)] = name
        return out
