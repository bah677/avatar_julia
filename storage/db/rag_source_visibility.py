"""Публичность ссылок на источники RAG."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

VIS_PUBLIC = "public"
VIS_PRIVATE = "private"


class RagSourceVisibilityMixin:
    async def get_rag_source_visibility(
        self, source_type: str, source_key: str
    ) -> Optional[str]:
        st = (source_type or "").strip()
        sk = (source_key or "").strip()
        if not st or not sk:
            return None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT visibility FROM rag_source_visibility
                    WHERE source_type = $1 AND source_key = $2
                    """,
                    st,
                    sk,
                )
                return str(row["visibility"]) if row else None
        except Exception as e:
            logger.error("get_rag_source_visibility: %s", e)
            return None

    async def set_rag_source_visibility(
        self,
        *,
        source_type: str,
        source_key: str,
        visibility: str,
        label: str = "",
        decided_by: int = 0,
    ) -> bool:
        vis = (visibility or "").strip().lower()
        if vis not in (VIS_PUBLIC, VIS_PRIVATE):
            return False
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO rag_source_visibility (
                        source_type, source_key, visibility, label, decided_by
                    ) VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (source_type, source_key) DO UPDATE SET
                        visibility = EXCLUDED.visibility,
                        label = EXCLUDED.label,
                        decided_by = EXCLUDED.decided_by,
                        created_at = NOW()
                    """,
                    (source_type or "").strip(),
                    (source_key or "").strip(),
                    vis,
                    (label or "")[:500],
                    int(decided_by or 0),
                )
                await conn.execute(
                    """
                    DELETE FROM rag_source_visibility_pending
                    WHERE source_type = $1 AND source_key = $2
                    """,
                    (source_type or "").strip(),
                    (source_key or "").strip(),
                )
            return True
        except Exception as e:
            logger.error("set_rag_source_visibility: %s", e)
            return False

    async def get_or_create_rag_source_pending(
        self,
        *,
        source_type: str,
        source_key: str,
        label: str,
    ) -> Optional[Dict[str, Any]]:
        st = (source_type or "").strip()
        sk = (source_key or "").strip()
        if not st or not sk:
            return None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, source_type, source_key, label, notify_sent
                    FROM rag_source_visibility_pending
                    WHERE source_type = $1 AND source_key = $2
                    """,
                    st,
                    sk,
                )
                if row:
                    return dict(row)
                pid = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO rag_source_visibility_pending (
                        id, source_type, source_key, label
                    ) VALUES ($1, $2, $3, $4)
                    """,
                    pid,
                    st,
                    sk,
                    (label or "")[:500],
                )
                row = await conn.fetchrow(
                    "SELECT id, source_type, source_key, label, notify_sent FROM rag_source_visibility_pending WHERE id = $1",
                    pid,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_or_create_rag_source_pending: %s", e)
            return None

    async def mark_rag_source_pending_notified(self, pending_id: uuid.UUID) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE rag_source_visibility_pending
                    SET notify_sent = TRUE
                    WHERE id = $1
                    """,
                    pending_id,
                )
        except Exception as e:
            logger.error("mark_rag_source_pending_notified: %s", e)
