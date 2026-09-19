"""Кэш: что уже импортировали в RAG (почта, диск, …)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

IMPORT_TELEMOST_MAIL = "telemost_mail"
IMPORT_YANDEX_DISK = "yandex_disk"

STATUS_INDEXED = "indexed"
STATUS_IGNORED = "ignored"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"
STATUS_PENDING = "pending"


class RagImportCacheMixin:
    async def rag_import_cache_get(
        self, import_type: str, cache_key: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT import_type, cache_key, status, chunks_count, label,
                           error_message, metadata, first_seen_at, last_attempt_at
                    FROM rag_import_cache
                    WHERE import_type = $1 AND cache_key = $2
                    """,
                    (import_type or "").strip(),
                    (cache_key or "").strip(),
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("rag_import_cache_get: %s", e)
            return None

    async def rag_import_cache_should_skip(
        self, import_type: str, cache_key: str
    ) -> bool:
        row = await self.rag_import_cache_get(import_type, cache_key)
        if not row:
            return False
        return str(row.get("status") or "") in (
            STATUS_INDEXED,
            STATUS_IGNORED,
            STATUS_SKIPPED,
        )

    async def rag_import_cache_delete(
        self, import_type: str, cache_key: str
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                tag = await conn.execute(
                    """
                    DELETE FROM rag_import_cache
                     WHERE import_type = $1 AND cache_key = $2
                    """,
                    (import_type or "").strip(),
                    (cache_key or "").strip(),
                )
                return tag.endswith("1")
        except Exception as e:
            logger.error("rag_import_cache_delete: %s", e)
            return False

    async def rag_import_cache_upsert(
        self,
        *,
        import_type: str,
        cache_key: str,
        status: str,
        chunks_count: int = 0,
        label: str = "",
        error_message: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        try:
            meta_json = json.dumps(metadata or {}, ensure_ascii=False)
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO rag_import_cache (
                        import_type, cache_key, status, chunks_count, label,
                        error_message, metadata, last_attempt_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                    ON CONFLICT (import_type, cache_key) DO UPDATE SET
                        status = EXCLUDED.status,
                        chunks_count = EXCLUDED.chunks_count,
                        label = EXCLUDED.label,
                        error_message = EXCLUDED.error_message,
                        metadata = EXCLUDED.metadata,
                        last_attempt_at = NOW()
                    """,
                    (import_type or "").strip(),
                    (cache_key or "").strip(),
                    (status or STATUS_INDEXED).strip(),
                    int(chunks_count or 0),
                    (label or "")[:500],
                    (error_message or "")[:1000],
                    meta_json,
                )
            return True
        except Exception as e:
            logger.error("rag_import_cache_upsert: %s", e)
            return False
