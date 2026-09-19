"""Архив входящих медиафайлов: копия на диск + строка ``media_inbound_files``."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config

logger = logging.getLogger(__name__)


def _sha256_hex(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class MediaArchiveMixin:
    async def archive_inbound_media_file(
        self,
        *,
        user_id: int,
        chat_id: int,
        telegram_message_id: int,
        file_unique_id: Optional[str],
        file_id_at_capture: str,
        media_subtype: str,
        mime_type: Optional[str],
        file_size: Optional[int],
        duration_sec: Optional[int],
        source_path: str,
        messages_row_id: Optional[int] = None,
    ) -> Optional[int]:
        if not config.media_inbound_archive_enabled:
            return None
        root = Path(config.resolved_media_inbound_archive_root)
        root.mkdir(parents=True, exist_ok=True)
        if not os.path.isfile(source_path):
            return None

        def _prepare_and_copy() -> tuple[str, str]:
            sha = _sha256_hex(source_path)
            ext = Path(source_path).suffix or ".bin"
            now = datetime.utcnow()
            rel_dir = Path(str(user_id)) / f"{now:%Y}" / f"{now:%m}"
            dest_dir = root / rel_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{sha[:16]}_{media_subtype}{ext}"
            dest_path = dest_dir / fname
            shutil.copy2(source_path, dest_path)
            rel = str(rel_dir / fname).replace("\\", "/").lstrip("/")
            return sha, rel

        try:
            sha_hex, storage_relpath = await asyncio.to_thread(_prepare_and_copy)
        except Exception as e:  # noqa: BLE001
            logger.error("media archive copy failed: %s", e, exc_info=True)
            return None

        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO media_inbound_files (
                        user_id, chat_id, telegram_message_id, file_unique_id,
                        file_id_at_capture, media_subtype, mime_type, file_size,
                        duration_sec, sha256_hex, storage_relpath, messages_row_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (user_id, chat_id, telegram_message_id, sha256_hex)
                    DO UPDATE SET
                        messages_row_id = COALESCE(EXCLUDED.messages_row_id, media_inbound_files.messages_row_id),
                        file_id_at_capture = EXCLUDED.file_id_at_capture,
                        mime_type = COALESCE(EXCLUDED.mime_type, media_inbound_files.mime_type),
                        duration_sec = COALESCE(EXCLUDED.duration_sec, media_inbound_files.duration_sec)
                    RETURNING id
                    """,
                    user_id,
                    chat_id,
                    telegram_message_id,
                    file_unique_id,
                    file_id_at_capture,
                    media_subtype,
                    mime_type,
                    file_size,
                    duration_sec,
                    sha_hex,
                    storage_relpath,
                    messages_row_id,
                )
                if row:
                    return int(row["id"])
                return await conn.fetchval(
                    """
                    SELECT id FROM media_inbound_files
                    WHERE user_id=$1 AND chat_id=$2 AND telegram_message_id=$3 AND sha256_hex=$4
                    """,
                    user_id,
                    chat_id,
                    telegram_message_id,
                    sha_hex,
                )
        except Exception as e:  # noqa: BLE001
            logger.error("media_inbound_files insert failed: %s", e, exc_info=True)
            return None
