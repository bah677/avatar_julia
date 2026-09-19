"""Mixin: планы, черновики, версии, обратная связь."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

logger = logging.getLogger(__name__)


class ContentItemsMixin:
    async def insert_content_plan(self, **fields: Any) -> Optional[UUID]:
        pid = fields.get("id") or uuid.uuid4()
        if not isinstance(pid, UUID):
            pid = UUID(str(pid))
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO content_plans (
                        id, product_id, week_start, focus, mix,
                        chat_id, message_id, created_by
                    ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8)
                    """,
                    pid,
                    fields.get("product_id") or "",
                    fields["week_start"],
                    fields.get("focus") or "",
                    json.dumps(fields.get("mix") or {}, ensure_ascii=False),
                    fields.get("chat_id"),
                    fields.get("message_id"),
                    fields.get("created_by"),
                )
            return pid
        except Exception as e:
            logger.error("insert_content_plan: %s", e)
            return None

    async def get_content_plan(self, plan_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM content_plans WHERE id = $1", plan_id
            )
        return dict(row) if row else None

    async def update_content_plan_message(
        self, plan_id: UUID, *, chat_id: int, message_id: int
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE content_plans
                   SET chat_id = $2, message_id = $3
                 WHERE id = $1
                """,
                plan_id,
                int(chat_id),
                int(message_id),
            )

    async def insert_content_item(self, **fields: Any) -> Optional[UUID]:
        iid = fields.get("id") or uuid.uuid4()
        if not isinstance(iid, UUID):
            iid = UUID(str(iid))
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO content_items (
                        id, product_id, plan_id, format, card_ids, lesson_id,
                        angle, goal, planned_day, status, created_by
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,
                        $7,$8,$9,$10,$11
                    )
                    """,
                    iid,
                    fields.get("product_id") or "",
                    fields.get("plan_id"),
                    fields.get("format") or "tg_post",
                    list(fields.get("card_ids") or []),
                    fields.get("lesson_id"),
                    fields.get("angle") or "",
                    fields.get("goal") or "",
                    fields.get("planned_day"),
                    fields.get("status") or "planned",
                    fields.get("created_by"),
                )
            return iid
        except Exception as e:
            logger.error("insert_content_item: %s", e)
            return None

    async def get_content_item(self, item_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM content_items WHERE id = $1", item_id
            )
        return dict(row) if row else None

    async def list_plan_items(self, plan_id: UUID) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM content_items
                 WHERE plan_id = $1
                 ORDER BY planned_day NULLS LAST, created_at
                """,
                plan_id,
            )
        return [dict(r) for r in rows]

    async def update_content_item(self, item_id: UUID, **fields: Any) -> None:
        allowed = {
            "format",
            "card_ids",
            "lesson_id",
            "angle",
            "goal",
            "planned_day",
            "status",
            "current_version",
            "plan_id",
        }
        sets = []
        values: List[Any] = []
        i = 1
        for k, v in fields.items():
            if k not in allowed:
                continue
            i += 1
            sets.append(f"{k} = ${i}")
            values.append(v)
        if not sets:
            return
        sets.append("updated_at = NOW()")
        sql = f"UPDATE content_items SET {', '.join(sets)} WHERE id = $1"
        async with self.get_connection() as conn:
            await conn.execute(sql, item_id, *values)

    async def insert_content_version(self, **fields: Any) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO content_versions (
                        item_id, version, text, instruction, model,
                        prompt_tokens, completion_tokens, chat_id, message_ids
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    RETURNING id
                    """,
                    fields["item_id"],
                    int(fields.get("version") or 1),
                    fields.get("text") or "",
                    fields.get("instruction") or "",
                    fields.get("model") or "",
                    int(fields.get("prompt_tokens") or 0),
                    int(fields.get("completion_tokens") or 0),
                    fields.get("chat_id"),
                    list(fields.get("message_ids") or []),
                )
            return int(row["id"]) if row else None
        except Exception as e:
            logger.error("insert_content_version: %s", e)
            return None

    async def get_content_version(
        self, item_id: UUID, version: int
    ) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM content_versions
                 WHERE item_id = $1 AND version = $2
                """,
                item_id,
                int(version),
            )
        return dict(row) if row else None

    async def get_latest_content_version(self, item_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM content_versions
                 WHERE item_id = $1
                 ORDER BY version DESC
                 LIMIT 1
                """,
                item_id,
            )
        return dict(row) if row else None

    async def find_item_by_reply(
        self, chat_id: int, reply_message_id: int
    ) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT i.*, v.version AS reply_version, v.text AS reply_text
                  FROM content_versions v
                  JOIN content_items i ON i.id = v.item_id
                 WHERE v.chat_id = $1 AND $2 = ANY(v.message_ids)
                 ORDER BY v.created_at DESC
                 LIMIT 1
                """,
                int(chat_id),
                int(reply_message_id),
            )
        return dict(row) if row else None

    async def update_version_message_ids(
        self, item_id: UUID, version: int, *, chat_id: int, message_ids: Sequence[int]
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE content_versions
                   SET chat_id = $3, message_ids = $4
                 WHERE item_id = $1 AND version = $2
                """,
                item_id,
                int(version),
                int(chat_id),
                list(message_ids),
            )

    async def insert_content_feedback(self, **fields: Any) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO content_feedback (
                        item_id, version, user_id, kind, reason_code, reason_text
                    ) VALUES ($1,$2,$3,$4,$5,$6)
                    """,
                    fields["item_id"],
                    int(fields.get("version") or 0),
                    fields.get("user_id"),
                    fields.get("kind") or "down",
                    fields.get("reason_code") or "",
                    fields.get("reason_text") or "",
                )
        except Exception as e:
            logger.error("insert_content_feedback: %s", e)

    async def list_unfolded_feedback(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM content_feedback
                 WHERE kind = 'down' AND folded = FALSE
                 ORDER BY created_at
                 LIMIT $1
                """,
                int(limit),
            )
        return [dict(r) for r in rows]

    async def mark_feedback_folded(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE content_feedback SET folded = TRUE WHERE id = ANY($1::bigint[])",
                list(ids),
            )

    async def count_unfolded_feedback(self) -> int:
        async with self.get_connection() as conn:
            n = await conn.fetchval(
                """
                SELECT COUNT(*) FROM content_feedback
                 WHERE kind = 'down' AND folded = FALSE
                """
            )
        return int(n or 0)
