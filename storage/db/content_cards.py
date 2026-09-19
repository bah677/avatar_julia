"""Mixin: карточки идей."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

logger = logging.getLogger(__name__)


class ContentCardsMixin:
    async def insert_content_card(self, **fields: Any) -> Optional[UUID]:
        cid = fields.get("id") or uuid.uuid4()
        if not isinstance(cid, UUID):
            cid = UUID(str(cid))
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO content_cards (
                        id, product_id, source_id, lesson_id, type, title, text,
                        quote, anchor_sec, page, speaker, audience_pain,
                        funnel_stage, formats, score, frequency, related_source_ids,
                        status
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,
                        $8,$9,$10,$11,$12,
                        $13,$14,$15,$16,$17,
                        $18
                    )
                    """,
                    cid,
                    fields.get("product_id") or "",
                    fields["source_id"],
                    fields.get("lesson_id"),
                    fields.get("type") or "idea",
                    (fields.get("title") or "")[:200],
                    fields.get("text") or "",
                    fields.get("quote") or "",
                    fields.get("anchor_sec"),
                    fields.get("page"),
                    fields.get("speaker") or "expert",
                    fields.get("audience_pain") or "",
                    fields.get("funnel_stage") or "warmup",
                    list(fields.get("formats") or []),
                    float(fields.get("score") or 0),
                    int(fields.get("frequency") or 1),
                    list(fields.get("related_source_ids") or []),
                    fields.get("status") or "active",
                )
            return cid
        except Exception as e:
            logger.error("insert_content_card: %s", e)
            return None

    async def get_content_card(self, card_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM content_cards WHERE id = $1", card_id
            )
        return dict(row) if row else None

    async def list_content_cards(
        self,
        *,
        product_id: str,
        lesson_id: Optional[int] = None,
        card_type: Optional[str] = None,
        status: str = "active",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        clauses = ["product_id = $1", "status = $2"]
        args: List[Any] = [product_id, status]
        if lesson_id:
            args.append(int(lesson_id))
            clauses.append(f"lesson_id = ${len(args)}")
        if card_type:
            args.append(card_type)
            clauses.append(f"type = ${len(args)}")
        args.append(max(1, min(200, int(limit))))
        sql = f"""
            SELECT * FROM content_cards
             WHERE {' AND '.join(clauses)}
             ORDER BY created_at DESC
             LIMIT ${len(args)}
        """
        async with self.get_connection() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def list_cards_by_ids(self, ids: Sequence[UUID]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                "SELECT * FROM content_cards WHERE id = ANY($1::uuid[])",
                list(ids),
            )
        by_id = {r["id"]: dict(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    async def archive_cards_for_source(self, source_id: UUID) -> int:
        async with self.get_connection() as conn:
            tag = await conn.execute(
                """
                UPDATE content_cards SET status = 'archived'
                 WHERE source_id = $1 AND status = 'active'
                """,
                source_id,
            )
        try:
            return int(str(tag).split()[-1])
        except Exception:
            return 0

    async def bump_card_frequency(
        self, card_id: UUID, related_source_id: UUID
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE content_cards
                   SET frequency = frequency + 1,
                       related_source_ids = CASE
                           WHEN $2 = ANY(related_source_ids) THEN related_source_ids
                           ELSE array_append(related_source_ids, $2)
                       END
                 WHERE id = $1
                """,
                card_id,
                related_source_id,
            )

    async def mark_cards_used(self, card_ids: Sequence[UUID]) -> None:
        if not card_ids:
            return
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE content_cards
                   SET used_count = used_count + 1,
                       last_used_at = NOW()
                 WHERE id = ANY($1::uuid[])
                """,
                list(card_ids),
            )

    async def pick_plan_candidates(
        self,
        *,
        product_id: str,
        reuse_days: int = 60,
        min_score: float = 0.0,
        limit: int = 40,
        max_per_lesson: int = 3,
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                WITH ranked AS (
                    SELECT c.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(c.lesson_id, 0)
                               ORDER BY
                                   CASE WHEN c.type IN ('question','pain','insight')
                                        AND c.created_at > NOW() - INTERVAL '30 days'
                                        THEN 0 ELSE 1 END,
                                   c.frequency DESC,
                                   c.score DESC
                           ) AS rn
                    FROM content_cards c
                    WHERE c.product_id = $1
                      AND c.status = 'active'
                      AND c.score >= $2
                      AND (c.last_used_at IS NULL
                           OR c.last_used_at < NOW() - ($3 || ' days')::interval)
                )
                SELECT * FROM ranked
                 WHERE rn <= $4
                 ORDER BY
                    CASE WHEN type IN ('question','pain','insight')
                         AND created_at > NOW() - INTERVAL '30 days'
                         THEN 0 ELSE 1 END,
                    frequency DESC,
                    score DESC
                 LIMIT $5
                """,
                product_id,
                float(min_score),
                str(int(reuse_days)),
                int(max_per_lesson),
                int(limit),
            )
        return [dict(r) for r in rows]

    async def count_cards_for_source(self, source_id: UUID) -> int:
        async with self.get_connection() as conn:
            n = await conn.fetchval(
                """
                SELECT COUNT(*) FROM content_cards
                 WHERE source_id = $1 AND status = 'active'
                """,
                source_id,
            )
        return int(n or 0)
