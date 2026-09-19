"""Mixin: уроки и источники курса."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

logger = logging.getLogger(__name__)

_QUEUE_STATUSES = ("new", "extracted", "indexed", "error")
_STALE_FETCH = ("fetching",)
_STALE_MINE = ("mining",)


def _pg_json(value: Any, default: Any) -> Any:
    """asyncpg отдаёт jsonb строкой, если кодек не задан."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, memoryview)):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _source_row(row: Any) -> Dict[str, Any]:
    d = dict(row)
    d["metadata"] = _pg_json(d.get("metadata"), {})
    if not isinstance(d["metadata"], dict):
        d["metadata"] = {}
    d["alt_urls"] = _pg_json(d.get("alt_urls"), [])
    if not isinstance(d["alt_urls"], list):
        d["alt_urls"] = []
    return d


def _lesson_row(row: Any) -> Dict[str, Any]:
    d = dict(row)
    d["passport"] = _pg_json(d.get("passport"), {})
    if not isinstance(d["passport"], dict):
        d["passport"] = {}
    return d


class CourseMixin:
    async def upsert_course_lesson(
        self,
        *,
        product_id: str,
        lesson_key: str,
        lesson_no: int,
        module_no: Optional[int] = None,
        title: str = "",
        disk_path: Optional[str] = None,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO course_lessons (
                        product_id, lesson_key, module_no, lesson_no, title, disk_path
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (product_id, lesson_key) DO UPDATE SET
                        module_no = COALESCE(EXCLUDED.module_no, course_lessons.module_no),
                        lesson_no = EXCLUDED.lesson_no,
                        title = CASE
                            WHEN EXCLUDED.title <> '' THEN EXCLUDED.title
                            ELSE course_lessons.title
                        END,
                        disk_path = COALESCE(EXCLUDED.disk_path, course_lessons.disk_path),
                        updated_at = NOW()
                    RETURNING id
                    """,
                    product_id,
                    lesson_key,
                    module_no,
                    int(lesson_no),
                    (title or "")[:500],
                    disk_path,
                )
                return int(row["id"]) if row else None
        except Exception as e:
            logger.error("upsert_course_lesson: %s", e)
            return None

    async def get_course_lesson(
        self, *, product_id: str, lesson_key: str
    ) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM course_lessons
                 WHERE product_id = $1 AND lesson_key = $2
                """,
                product_id,
                lesson_key,
            )
        return _lesson_row(row) if row else None

    async def get_course_lesson_by_id(self, lesson_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM course_lessons WHERE id = $1", int(lesson_id)
            )
        return _lesson_row(row) if row else None

    async def list_course_lessons(self, product_id: str) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM course_lessons
                 WHERE product_id = $1
                 ORDER BY module_no NULLS LAST, lesson_no, lesson_key
                """,
                product_id,
            )
        return [_lesson_row(r) for r in rows]

    async def update_lesson_passport(
        self,
        lesson_id: int,
        *,
        passport: Dict[str, Any],
        passport_text: str,
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE course_lessons
                   SET passport = $2::jsonb,
                       passport_text = $3,
                       passport_updated_at = NOW(),
                       updated_at = NOW()
                 WHERE id = $1
                """,
                int(lesson_id),
                json.dumps(passport or {}, ensure_ascii=False),
                passport_text or "",
            )

    async def insert_course_source(self, **fields: Any) -> Optional[UUID]:
        sid = fields.get("id") or uuid.uuid4()
        if not isinstance(sid, UUID):
            sid = UUID(str(sid))
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO course_sources (
                        id, product_id, origin, kind, lesson_id, title,
                        disk_path, disk_etag, video_id, url, alt_urls,
                        platform, duration_sec, recorded_on, status,
                        added_by, metadata, intake_chat_id, intake_message_id
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,
                        $7,$8,$9,$10,$11::jsonb,
                        $12,$13,$14,$15,
                        $16,$17::jsonb,$18,$19
                    )
                    """,
                    sid,
                    fields.get("product_id") or "",
                    fields.get("origin") or "disk",
                    fields.get("kind") or "other",
                    fields.get("lesson_id"),
                    (fields.get("title") or "")[:500],
                    fields.get("disk_path"),
                    fields.get("disk_etag") or "",
                    fields.get("video_id"),
                    fields.get("url") or "",
                    json.dumps(fields.get("alt_urls") or [], ensure_ascii=False),
                    fields.get("platform") or "",
                    fields.get("duration_sec"),
                    fields.get("recorded_on"),
                    fields.get("status") or "new",
                    fields.get("added_by"),
                    json.dumps(fields.get("metadata") or {}, ensure_ascii=False),
                    fields.get("intake_chat_id"),
                    fields.get("intake_message_id"),
                )
            return sid
        except Exception as e:
            logger.error("insert_course_source: %s", e)
            return None

    async def get_course_source(self, source_id: UUID) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM course_sources WHERE id = $1", source_id
            )
        return _source_row(row) if row else None

    async def get_course_source_by_video(
        self, origin: str, video_id: str
    ) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM course_sources
                 WHERE origin = $1 AND video_id = $2
                """,
                origin,
                video_id,
            )
        return _source_row(row) if row else None

    async def get_course_source_by_disk_path(self, disk_path: str) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM course_sources WHERE disk_path = $1",
                disk_path,
            )
        return _source_row(row) if row else None

    async def list_course_sources_by_product(
        self, product_ids: Sequence[str], *, statuses: Optional[Sequence[str]] = None
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            if statuses:
                rows = await conn.fetch(
                    """
                    SELECT * FROM course_sources
                     WHERE product_id = ANY($1::text[])
                       AND status = ANY($2::text[])
                     ORDER BY created_at
                    """,
                    list(product_ids),
                    list(statuses),
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM course_sources
                     WHERE product_id = ANY($1::text[])
                     ORDER BY created_at
                    """,
                    list(product_ids),
                )
        return [_source_row(r) for r in rows]

    async def list_course_sources_for_lesson(self, lesson_id: int) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM course_sources
                 WHERE lesson_id = $1 AND status <> 'deleted'
                 ORDER BY kind, created_at
                """,
                int(lesson_id),
            )
        return [_source_row(r) for r in rows]

    async def find_similar_video_source(
        self,
        *,
        product_id: str,
        kind: str,
        lesson_id: Optional[int],
        duration_sec: int,
        exclude_id: Optional[UUID] = None,
        tolerance: float = 0.02,
    ) -> Optional[Dict[str, Any]]:
        if not duration_sec:
            return None
        lo = int(duration_sec * (1 - tolerance))
        hi = int(duration_sec * (1 + tolerance))
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM course_sources
                 WHERE product_id = $1
                   AND kind = $2
                   AND ($3::int IS NULL OR lesson_id = $3)
                   AND duration_sec BETWEEN $4 AND $5
                   AND status NOT IN ('deleted', 'skipped')
                   AND ($6::uuid IS NULL OR id <> $6)
                 ORDER BY CASE origin
                     WHEN 'youtube' THEN 0
                     WHEN 'vimeo' THEN 1
                     WHEN 'kinescope' THEN 2
                     ELSE 3
                 END
                 LIMIT 1
                """,
                product_id,
                kind,
                lesson_id,
                lo,
                hi,
                exclude_id,
            )
        return _source_row(row) if row else None

    async def update_course_source(self, source_id: UUID, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "product_id",
            "origin",
            "kind",
            "lesson_id",
            "title",
            "disk_path",
            "disk_etag",
            "video_id",
            "url",
            "alt_urls",
            "superseded_by",
            "platform",
            "duration_sec",
            "recorded_on",
            "status",
            "attempts",
            "next_attempt_at",
            "error_message",
            "text_method",
            "chars_count",
            "chunks_count",
            "cards_count",
            "intake_chat_id",
            "intake_message_id",
            "added_by",
            "metadata",
            "processed_at",
        }
        sets = []
        values: List[Any] = []
        i = 1
        for k, v in fields.items():
            if k not in allowed:
                continue
            i += 1
            if k in ("alt_urls", "metadata"):
                sets.append(f"{k} = ${i}::jsonb")
                values.append(json.dumps(v if v is not None else ({} if k == "metadata" else []), ensure_ascii=False))
            else:
                sets.append(f"{k} = ${i}")
                values.append(v)
        if not sets:
            return
        sets.append("updated_at = NOW()")
        sql = f"UPDATE course_sources SET {', '.join(sets)} WHERE id = $1"
        async with self.get_connection() as conn:
            await conn.execute(sql, source_id, *values)

    async def claim_next_course_source(
        self,
        product_ids: Sequence[str],
        *,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        ts = now or datetime.now(timezone.utc)
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM course_sources
                 WHERE product_id = ANY($1::text[])
                   AND status IN ('new', 'extracted', 'indexed')
                   AND (next_attempt_at IS NULL OR next_attempt_at <= $2)
                 ORDER BY created_at
                 LIMIT 1
                """,
                list(product_ids),
                ts,
            )
        return _source_row(row) if row else None

    async def recover_stale_course_sources(self, *, older_than_min: int = 30) -> int:
        async with self.get_connection() as conn:
            r1 = await conn.execute(
                """
                UPDATE course_sources
                   SET status = 'new', updated_at = NOW()
                 WHERE status = 'fetching'
                   AND updated_at < NOW() - ($1 || ' minutes')::interval
                """,
                str(int(older_than_min)),
            )
            r2 = await conn.execute(
                """
                UPDATE course_sources
                   SET status = 'indexed', updated_at = NOW()
                 WHERE status = 'mining'
                   AND updated_at < NOW() - ($1 || ' minutes')::interval
                """,
                str(int(older_than_min)),
            )
        n = 0
        for tag in (r1, r2):
            try:
                n += int(str(tag).split()[-1])
            except Exception:
                pass
        return n

    async def list_course_queue(self, product_ids: Sequence[str]) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM course_sources
                 WHERE product_id = ANY($1::text[])
                   AND status IN ('new','fetching','extracted','indexed','mining','error')
                 ORDER BY
                    CASE status
                        WHEN 'error' THEN 0
                        WHEN 'fetching' THEN 1
                        WHEN 'mining' THEN 2
                        ELSE 3
                    END,
                    created_at
                """,
                list(product_ids),
            )
        return [_source_row(r) for r in rows]

    async def append_source_alt_url(self, source_id: UUID, url: str) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE course_sources
                   SET alt_urls = (
                        SELECT jsonb_agg(DISTINCT x)
                          FROM jsonb_array_elements_text(alt_urls || $2::jsonb) AS t(x)
                   ),
                       updated_at = NOW()
                 WHERE id = $1
                """,
                source_id,
                json.dumps([url], ensure_ascii=False),
            )
