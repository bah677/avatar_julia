"""Состояние импорта с Яндекс.Диска."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class YandexDiskMixin:
    async def get_yandex_disk_indexed(
        self, source_id: str, remote_path: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT source_id, remote_path, file_name, etag, file_size,
                           chunks_count, indexed_at, metadata
                    FROM yandex_disk_indexed_files
                    WHERE source_id = $1 AND remote_path = $2
                    """,
                    source_id,
                    remote_path,
                )
                if not row:
                    return None
                meta = row["metadata"]
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except json.JSONDecodeError:
                        meta = {}
                return {
                    "source_id": row["source_id"],
                    "remote_path": row["remote_path"],
                    "file_name": row["file_name"],
                    "etag": row["etag"],
                    "file_size": row["file_size"],
                    "chunks_count": row["chunks_count"],
                    "indexed_at": row["indexed_at"],
                    "metadata": meta or {},
                }
        except Exception as e:
            logger.error("get_yandex_disk_indexed: %s", e)
            return None

    async def upsert_yandex_disk_indexed(
        self,
        *,
        source_id: str,
        remote_path: str,
        file_name: str,
        etag: str = "",
        file_size: int = 0,
        chunks_count: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        try:
            meta_json = json.dumps(metadata or {}, ensure_ascii=False)
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO yandex_disk_indexed_files (
                        source_id, remote_path, file_name, etag, file_size,
                        chunks_count, metadata, indexed_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, NOW())
                    ON CONFLICT (source_id, remote_path) DO UPDATE SET
                        file_name = EXCLUDED.file_name,
                        etag = EXCLUDED.etag,
                        file_size = EXCLUDED.file_size,
                        chunks_count = EXCLUDED.chunks_count,
                        metadata = EXCLUDED.metadata,
                        indexed_at = NOW()
                    """,
                    source_id,
                    remote_path,
                    file_name,
                    etag or "",
                    int(file_size or 0),
                    int(chunks_count or 0),
                    meta_json,
                )
            return True
        except Exception as e:
            logger.error("upsert_yandex_disk_indexed: %s", e)
            return False

    async def list_yandex_disk_indexed_stats(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT source_id,
                           COUNT(*) AS files_count,
                           COALESCE(SUM(chunks_count), 0) AS chunks_total,
                           MAX(indexed_at) AS last_indexed_at
                    FROM yandex_disk_indexed_files
                    GROUP BY source_id
                    ORDER BY source_id
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_yandex_disk_indexed_stats: %s", e)
            return []
