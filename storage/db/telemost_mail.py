"""Письма Телемоста: pending и состояние IMAP."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Optional

from telemost_mail.classifier_llm import TelemostClassification

logger = logging.getLogger(__name__)


class TelemostMailMixin:
    async def get_telemost_mail_last_uid(self) -> int:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT last_imap_uid FROM telemost_mail_state WHERE id = 1"
                )
                return int(row["last_imap_uid"]) if row else 0
        except Exception as e:
            logger.error("get_telemost_mail_last_uid: %s", e)
            return 0

    async def set_telemost_mail_last_uid(self, uid: int) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO telemost_mail_state (id, last_imap_uid, updated_at)
                    VALUES (1, $1, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        last_imap_uid = GREATEST(telemost_mail_state.last_imap_uid, EXCLUDED.last_imap_uid),
                        updated_at = NOW()
                    """,
                    int(uid),
                )
        except Exception as e:
            logger.error("set_telemost_mail_last_uid: %s", e)

    async def telemost_mail_uid_exists(self, imap_uid: str) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT 1 FROM telemost_mail_pending WHERE imap_uid = $1",
                    str(imap_uid),
                )
                return row is not None
        except Exception as e:
            logger.error("telemost_mail_uid_exists: %s", e)
            return False

    async def telemost_mail_message_id_exists(self, message_id: str) -> bool:
        mid = (message_id or "").strip()
        if not mid:
            return False
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT 1 FROM telemost_mail_pending WHERE message_id = $1",
                    mid,
                )
                return row is not None
        except Exception as e:
            logger.error("telemost_mail_message_id_exists: %s", e)
            return False

    async def insert_telemost_mail_pending(
        self,
        *,
        pending_id: uuid.UUID,
        imap_uid: str,
        message_id: str,
        subject: str,
        sender: str,
        body_summary: str,
        transcript_text: str,
        classification: TelemostClassification,
        notify_chat_id: int,
        notify_topic_id: int,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        clf = {
            "is_club_meeting": classification.is_club_meeting,
            "recommend_index": classification.recommend_index,
            "title": classification.title,
            "meeting_topic": classification.meeting_topic,
            "content_type": classification.content_type,
            "content_category": classification.content_category,
            "product": classification.product,
            "tags": classification.tags,
            "summary": classification.summary,
            "admin_note": classification.admin_note,
            "reason": classification.reason,
        }
        if extra_metadata:
            clf["extra"] = extra_metadata
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO telemost_mail_pending (
                        id, imap_uid, message_id, subject, sender,
                        body_summary, transcript_text, classification,
                        notify_chat_id, notify_topic_id
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10
                    )
                    ON CONFLICT (imap_uid) DO NOTHING
                    """,
                    pending_id,
                    str(imap_uid),
                    message_id or "",
                    subject or "",
                    sender or "",
                    body_summary or "",
                    transcript_text or "",
                    json.dumps(clf, ensure_ascii=False),
                    int(notify_chat_id),
                    int(notify_topic_id),
                )
            return True
        except Exception as e:
            logger.error("insert_telemost_mail_pending: %s", e)
            return False

    async def get_telemost_mail_pending(
        self, pending_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM telemost_mail_pending WHERE id = $1",
                    pending_id,
                )
                if not row:
                    return None
                d = dict(row)
                clf = d.get("classification")
                if isinstance(clf, str):
                    try:
                        d["classification"] = json.loads(clf)
                    except json.JSONDecodeError:
                        d["classification"] = {}
                clf_dict = d.get("classification") or {}
                if isinstance(clf_dict, dict):
                    d["extra_metadata"] = clf_dict.get("extra") or {}
                return d
        except Exception as e:
            logger.error("get_telemost_mail_pending: %s", e)
            return None

    async def get_telemost_mail_pending_by_imap_uid(
        self, imap_uid: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM telemost_mail_pending WHERE imap_uid = $1 ORDER BY created_at DESC LIMIT 1",
                    str(imap_uid),
                )
                if not row:
                    return None
                d = dict(row)
                clf = d.get("classification")
                if isinstance(clf, str):
                    try:
                        d["classification"] = json.loads(clf)
                    except json.JSONDecodeError:
                        d["classification"] = {}
                clf_dict = d.get("classification") or {}
                if isinstance(clf_dict, dict):
                    d["extra_metadata"] = clf_dict.get("extra") or {}
                return d
        except Exception as e:
            logger.error("get_telemost_mail_pending_by_imap_uid: %s", e)
            return None

    async def update_telemost_mail_pending_status(
        self,
        pending_id: uuid.UUID,
        *,
        status: str,
        chunks_count: int = 0,
        error: str = "",
        notify_message_id: Optional[int] = None,
    ) -> None:
        try:
            async with self.get_connection() as conn:
                if notify_message_id is not None:
                    await conn.execute(
                        """
                        UPDATE telemost_mail_pending SET
                            status = $2,
                            chunks_count = $3,
                            error_message = $4,
                            notify_message_id = $5,
                            resolved_at = CASE WHEN $2 IN ('indexed','ignored','error') THEN NOW() ELSE resolved_at END
                        WHERE id = $1
                        """,
                        pending_id,
                        status,
                        int(chunks_count),
                        (error or "")[:1000],
                        int(notify_message_id),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE telemost_mail_pending SET
                            status = $2,
                            chunks_count = $3,
                            error_message = $4,
                            resolved_at = CASE WHEN $2 IN ('indexed','ignored','error') THEN NOW() ELSE resolved_at END
                        WHERE id = $1
                        """,
                        pending_id,
                        status,
                        int(chunks_count),
                        (error or "")[:1000],
                    )
        except Exception as e:
            logger.error("update_telemost_mail_pending_status: %s", e)

    async def upsert_telemost_recording(
        self,
        *,
        meeting_id: str,
        imap_uid: str,
        message_id: str = "",
        subject: str = "",
        video_url: str = "",
        audio_url: str = "",
        linked_pending_id: Optional[uuid.UUID] = None,
    ) -> None:
        mid = (meeting_id or "").strip()
        if not mid:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO telemost_mail_recordings (
                        meeting_id, imap_uid, message_id, subject, video_url, audio_url,
                        linked_pending_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (meeting_id) DO UPDATE SET
                        imap_uid = EXCLUDED.imap_uid,
                        message_id = COALESCE(NULLIF(EXCLUDED.message_id, ''), telemost_mail_recordings.message_id),
                        subject = COALESCE(NULLIF(EXCLUDED.subject, ''), telemost_mail_recordings.subject),
                        video_url = COALESCE(NULLIF(EXCLUDED.video_url, ''), telemost_mail_recordings.video_url),
                        audio_url = COALESCE(NULLIF(EXCLUDED.audio_url, ''), telemost_mail_recordings.audio_url),
                        linked_pending_id = COALESCE(
                            EXCLUDED.linked_pending_id, telemost_mail_recordings.linked_pending_id
                        )
                    """,
                    mid,
                    str(imap_uid or ""),
                    message_id or "",
                    subject or "",
                    video_url or "",
                    audio_url or "",
                    linked_pending_id,
                )
        except Exception as e:
            logger.error("upsert_telemost_recording: %s", e)

    async def get_telemost_recording(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        mid = (meeting_id or "").strip()
        if not mid:
            return None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM telemost_mail_recordings WHERE meeting_id = $1",
                    mid,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_telemost_recording: %s", e)
            return None

    async def telemost_recording_imap_uid_exists(self, imap_uid: str) -> bool:
        uid = (imap_uid or "").strip()
        if not uid:
            return False
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT 1 FROM telemost_mail_recordings WHERE imap_uid = $1",
                    uid,
                )
                return row is not None
        except Exception as e:
            logger.error("telemost_recording_imap_uid_exists: %s", e)
            return False

    async def set_telemost_recording_local_path(
        self, meeting_id: str, local_path: str
    ) -> None:
        mid = (meeting_id or "").strip()
        if not mid:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE telemost_mail_recordings
                    SET local_path = $2, downloaded_at = NOW()
                    WHERE meeting_id = $1
                    """,
                    mid,
                    (local_path or "").strip(),
                )
        except Exception as e:
            logger.error("set_telemost_recording_local_path: %s", e)

    async def set_telemost_recording_local_audio_path(
        self, meeting_id: str, local_path: str
    ) -> None:
        mid = (meeting_id or "").strip()
        if not mid:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE telemost_mail_recordings
                    SET local_audio_path = $2, audio_downloaded_at = NOW()
                    WHERE meeting_id = $1
                    """,
                    mid,
                    (local_path or "").strip(),
                )
        except Exception as e:
            logger.error("set_telemost_recording_local_audio_path: %s", e)

    async def link_recording_to_pending(
        self, meeting_id: str, pending_id: uuid.UUID
    ) -> None:
        mid = (meeting_id or "").strip()
        if not mid:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE telemost_mail_recordings
                    SET linked_pending_id = $2
                    WHERE meeting_id = $1
                    """,
                    mid,
                    pending_id,
                )
        except Exception as e:
            logger.error("link_recording_to_pending: %s", e)

    async def get_telemost_pending_by_meeting_id(
        self, meeting_id: str
    ) -> Optional[Dict[str, Any]]:
        mid = (meeting_id or "").strip()
        if not mid:
            return None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM telemost_mail_pending
                    WHERE classification->'extra'->>'meeting_id' = $1
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    mid,
                )
                if not row:
                    return None
                d = dict(row)
                clf = d.get("classification")
                if isinstance(clf, str):
                    try:
                        d["classification"] = json.loads(clf)
                    except json.JSONDecodeError:
                        d["classification"] = {}
                clf_dict = d.get("classification") or {}
                if isinstance(clf_dict, dict):
                    d["extra_metadata"] = clf_dict.get("extra") or {}
                return d
        except Exception as e:
            logger.error("get_telemost_pending_by_meeting_id: %s", e)
            return None

    async def get_last_indexed_telemost_mail(self) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM telemost_mail_pending
                    WHERE status = 'indexed'
                    ORDER BY resolved_at DESC NULLS LAST, created_at DESC
                    LIMIT 1
                    """
                )
                if not row:
                    return None
                d = dict(row)
                clf = d.get("classification")
                if isinstance(clf, str):
                    try:
                        d["classification"] = json.loads(clf)
                    except json.JSONDecodeError:
                        d["classification"] = {}
                clf_dict = d.get("classification") or {}
                if isinstance(clf_dict, dict):
                    d["extra_metadata"] = clf_dict.get("extra") or {}
                return d
        except Exception as e:
            logger.error("get_last_indexed_telemost_mail: %s", e)
            return None

    async def set_telemost_notify_message_id(
        self, pending_id: uuid.UUID, message_id: int
    ) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE telemost_mail_pending
                    SET notify_message_id = $2
                    WHERE id = $1
                    """,
                    pending_id,
                    int(message_id),
                )
        except Exception as e:
            logger.error("set_telemost_notify_message_id: %s", e)
