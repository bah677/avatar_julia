"""Mixin: чаты веб-студии и их сообщения."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

_ALLOWED_UPDATE = frozenset({"title", "format", "stage", "focus", "context", "archived"})


def _as_uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class WebChatsMixin:
    async def create_web_chat(self, **fields: Any) -> Optional[UUID]:
        cid = _as_uuid(fields.get("id") or uuid.uuid4())
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO web_chats (
                        id, product_id, title, format, stage, focus, context, created_by
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                    """,
                    cid,
                    fields.get("product_id") or "",
                    (fields.get("title") or "")[:200],
                    fields.get("format") or "stories",
                    fields.get("stage") or "warmup",
                    fields.get("focus") or "",
                    json.dumps(fields.get("context") or {}, ensure_ascii=False),
                    fields.get("created_by"),
                )
            return cid
        except Exception as e:
            logger.error("create_web_chat: %s", e)
            return None

    async def get_web_chat(self, chat_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM web_chats WHERE id = $1", _as_uuid(chat_id)
            )
        return _decode_chat(row)

    async def list_web_chats(
        self,
        product_id: str,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        sql = """
            SELECT c.*,
                   (SELECT COUNT(*) FROM web_chat_messages m WHERE m.chat_id = c.id) AS messages_count
              FROM web_chats c
             WHERE c.product_id = $1
               AND ($2 OR c.archived = FALSE)
             ORDER BY c.updated_at DESC
             LIMIT $3
        """
        async with self.get_connection() as conn:
            rows = await conn.fetch(sql, product_id, bool(include_archived), int(limit))
        return [_decode_chat(r) for r in rows]

    async def update_web_chat(self, chat_id: UUID, **fields: Any) -> None:
        sets: List[str] = []
        args: List[Any] = [_as_uuid(chat_id)]
        for key, value in fields.items():
            if key not in _ALLOWED_UPDATE:
                continue
            args.append(
                json.dumps(value or {}, ensure_ascii=False) if key == "context" else value
            )
            cast = "::jsonb" if key == "context" else ""
            sets.append(f"{key} = ${len(args)}{cast}")
        if not sets:
            return
        sets.append("updated_at = NOW()")
        async with self.get_connection() as conn:
            await conn.execute(
                f"UPDATE web_chats SET {', '.join(sets)} WHERE id = $1", *args
            )

    async def touch_web_chat(self, chat_id: UUID) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE web_chats SET updated_at = NOW() WHERE id = $1", _as_uuid(chat_id)
            )

    async def delete_web_chat(self, chat_id: UUID) -> None:
        async with self.get_connection() as conn:
            await conn.execute("DELETE FROM web_chats WHERE id = $1", _as_uuid(chat_id))

    async def insert_web_chat_message(self, **fields: Any) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                mid = await conn.fetchval(
                    """
                    INSERT INTO web_chat_messages (
                        chat_id, role, text, model,
                        prompt_tokens, completion_tokens, meta
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
                    RETURNING id
                    """,
                    _as_uuid(fields["chat_id"]),
                    fields.get("role") or "user",
                    fields.get("text") or "",
                    fields.get("model") or "",
                    int(fields.get("prompt_tokens") or 0),
                    int(fields.get("completion_tokens") or 0),
                    json.dumps(fields.get("meta") or {}, ensure_ascii=False),
                )
                await conn.execute(
                    "UPDATE web_chats SET updated_at = NOW() WHERE id = $1",
                    _as_uuid(fields["chat_id"]),
                )
            return int(mid) if mid else None
        except Exception as e:
            logger.error("insert_web_chat_message: %s", e)
            return None

    async def get_web_chat_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM web_chat_messages WHERE id = $1", int(message_id)
            )
        return _decode_message(row) if row else None

    async def update_web_chat_message_meta(self, message_id: int, meta: Dict[str, Any]) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE web_chat_messages SET meta = $2::jsonb WHERE id = $1",
                int(message_id),
                json.dumps(meta or {}, ensure_ascii=False),
            )

    async def list_web_chat_messages(
        self, chat_id: UUID, *, limit: int = 500
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM web_chat_messages
                 WHERE chat_id = $1
                 ORDER BY id
                 LIMIT $2
                """,
                _as_uuid(chat_id),
                int(limit),
            )
        return [_decode_message(r) for r in rows]

    async def last_web_chat_messages(
        self, chat_id: UUID, *, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Последние N сообщений в прямом порядке — для истории в промпте."""
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM (
                    SELECT * FROM web_chat_messages
                     WHERE chat_id = $1
                     ORDER BY id DESC
                     LIMIT $2
                ) t ORDER BY id
                """,
                _as_uuid(chat_id),
                int(limit),
            )
        return [_decode_message(r) for r in rows]


def _decode_chat(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    data["context"] = _decode_json(data.get("context"))
    return data


def _decode_message(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["meta"] = _decode_json(data.get("meta"))
    return data


def _decode_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, memoryview)):
        raw = bytes(raw).decode("utf-8")
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
