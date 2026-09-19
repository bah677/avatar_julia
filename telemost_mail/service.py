"""Оркестрация: IMAP → классификация → ожидание админа → RAG."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, List, Optional, Sequence, TYPE_CHECKING

from bot.utils.rag_admin_context import rag_admin_chat_id, rag_admin_topic_id
from storage.db.rag_import_cache import (
    IMPORT_TELEMOST_MAIL,
    STATUS_ERROR,
    STATUS_IGNORED,
    STATUS_INDEXED,
    STATUS_SKIPPED,
)
from telemost_mail.backfill_stats import BackfillStats
from telemost_mail.cache_keys import telemost_mail_cache_key
from telemost_mail.classifier_llm import TelemostClassification, classify_telemost_summary
from telemost_mail.decisions import register_mail_decision_wait, wait_mail_decision
from telemost_mail.email_parse import parse_telemost_email
from telemost_mail.imap_client import FetchedMail, YandexImapClient
from telemost_mail.format_result import format_telemost_index_result_html
from telemost_mail.recording_parse import RecordingMailParsed, parse_recording_email, _url_video_score
from config import config
from telemost_mail.transcript import extract_expert_speech, extract_expert_speech_llm_fallback

if TYPE_CHECKING:
    from rag.material_index import MaterialIndexService

logger = logging.getLogger(__name__)

NotifyCallback = Callable[[dict[str, Any]], Awaitable[None]]


class TelemostMailService:
    def __init__(
        self,
        *,
        imap: YandexImapClient,
        user_storage,
        material_index: Optional["MaterialIndexService"],
        from_markers: Sequence[str],
        club_hint: str,
        speaker_names: Sequence[str],
        default_product: str = "",
        notify_chat_id: int = 0,
        notify_topic_id: int = 0,
    ):
        self._imap = imap
        self._storage = user_storage
        self._index = material_index
        self._from_markers = tuple(from_markers)
        self._club_hint = club_hint
        self._speaker_names = list(speaker_names)
        self._default_product = default_product
        self._notify_chat_id = int(notify_chat_id or 0)
        self._notify_topic_id = int(notify_topic_id or 0)
        self._bot_app: Any = None

    def set_bot_app(self, app: Any) -> None:
        self._bot_app = app

    @classmethod
    def from_config(cls, config, *, user_storage, material_index) -> "TelemostMailService":
        markers = [
            m.strip()
            for m in (getattr(config, "TELEMOST_MAIL_FROM_MARKERS", "") or "telemost,телемост").split(",")
            if m.strip()
        ]
        speakers = [
            m.strip()
            for m in (getattr(config, "TELEMOST_MAIL_AVATAR_SPEAKER_NAMES", "") or "").split(",")
            if m.strip()
        ]
        return cls(
            imap=YandexImapClient(
                getattr(config, "TELEMOST_MAIL_LOGIN", "") or "",
                getattr(config, "TELEMOST_MAIL_PASSWORD", "") or "",
                host=getattr(config, "TELEMOST_MAIL_IMAP_HOST", "imap.yandex.ru") or "imap.yandex.ru",
                port=int(getattr(config, "TELEMOST_MAIL_IMAP_PORT", 993) or 993),
                folder=getattr(config, "TELEMOST_MAIL_FOLDER", "INBOX") or "INBOX",
            ),
            user_storage=user_storage,
            material_index=material_index,
            from_markers=markers or ("keeper@telemost.yandex.ru",),
            club_hint=getattr(config, "TELEMOST_MAIL_CLUB_HINT", "") or "",
            speaker_names=speakers,
            default_product=getattr(config, "TELEMOST_MAIL_DEFAULT_PRODUCT", "") or "",
            notify_chat_id=rag_admin_chat_id(),
            notify_topic_id=int(rag_admin_topic_id() or 0),
        )

    async def _mail_already_handled(self, mail: FetchedMail) -> bool:
        key = telemost_mail_cache_key(
            imap_uid=mail.uid, message_id=mail.message_id
        )
        if await self._storage.rag_import_cache_should_skip(
            IMPORT_TELEMOST_MAIL, key
        ):
            return True
        if await self._storage.telemost_mail_uid_exists(mail.uid):
            return True
        if mail.message_id and await self._storage.telemost_mail_message_id_exists(
            mail.message_id
        ):
            return True
        return False

    @staticmethod
    def _mail_has_txt_attachment(mail: FetchedMail) -> bool:
        """Конспект Телемоста — только во вложенном .txt."""
        return bool((mail.transcript_text or "").strip())

    async def _skip_mail_without_attachment(self, mail: FetchedMail) -> None:
        await self._record_mail_cache(
            imap_uid=mail.uid,
            message_id=mail.message_id,
            status=STATUS_SKIPPED,
            label=(mail.subject or "")[:200],
            error_message="Нет TXT-вложения с расшифровкой",
        )
        logger.info(
            "telemost_mail: пропуск без TXT uid=%s subject=%r",
            mail.uid,
            (mail.subject or "")[:120],
        )

    async def _record_mail_cache(
        self,
        *,
        imap_uid: str,
        message_id: str,
        status: str,
        chunks_count: int = 0,
        label: str = "",
        error_message: str = "",
    ) -> None:
        await self._storage.rag_import_cache_upsert(
            import_type=IMPORT_TELEMOST_MAIL,
            cache_key=telemost_mail_cache_key(
                imap_uid=imap_uid, message_id=message_id
            ),
            status=status,
            chunks_count=chunks_count,
            label=label,
            error_message=error_message,
        )

    async def _create_pending_from_mail(
        self, mail: FetchedMail
    ) -> tuple[uuid.UUID, dict[str, Any]]:
        parsed = parse_telemost_email(mail.subject, mail.body_text)
        llm_body = parsed.context_for_llm() or mail.body_text
        classification = await classify_telemost_summary(
            subject=mail.subject,
            body_summary=llm_body,
            club_hint=self._club_hint,
            default_product=self._default_product,
        )
        pending_id = uuid.uuid4()
        await self._storage.insert_telemost_mail_pending(
            pending_id=pending_id,
            imap_uid=mail.uid,
            message_id=mail.message_id,
            subject=mail.subject,
            sender=mail.sender,
            body_summary=llm_body,
            transcript_text=mail.transcript_text,
            classification=classification,
            notify_chat_id=self._notify_chat_id,
            notify_topic_id=self._notify_topic_id,
            extra_metadata={
                "meeting_id": parsed.meeting_id,
                "meeting_date": parsed.meeting_date,
                "meeting_url": parsed.meeting_url,
            },
        )
        if parsed.meeting_id:
            await self._storage.link_recording_to_pending(
                parsed.meeting_id, pending_id
            )
        note = {
            "pending_id": str(pending_id),
            "subject": mail.subject,
            "classification": classification,
            "has_transcript": bool(mail.transcript_text.strip()),
            "meeting_id": parsed.meeting_id,
            "meeting_date": parsed.meeting_date,
            "backfill": False,
        }
        return pending_id, note

    async def _ingest_recording_mail(
        self, mail: FetchedMail, rec: RecordingMailParsed
    ) -> None:
        """Письмо «Запись встречи» — ссылка на видео, без TXT и без RAG-уведомления."""
        meeting_id = (rec.meeting_id or "").strip()
        if not meeting_id:
            logger.warning(
                "telemost recording without meeting_id uid=%s subject=%r",
                mail.uid,
                (mail.subject or "")[:80],
            )
            return
        existing = await self._storage.get_telemost_recording(meeting_id)
        video_url = rec.video_url or ""
        audio_url = rec.audio_url or ""
        if existing:
            old_url = (existing.get("video_url") or "").strip()
            if _url_video_score(old_url) > _url_video_score(video_url):
                video_url = old_url
            old_audio = (existing.get("audio_url") or "").strip()
            if _url_video_score(old_audio) > _url_video_score(audio_url):
                audio_url = old_audio
            elif (
                await self._storage.telemost_recording_imap_uid_exists(mail.uid)
                and old_url == video_url
                and old_audio == audio_url
            ):
                return
        pending = await self._storage.get_telemost_pending_by_meeting_id(meeting_id)
        linked: Optional[uuid.UUID] = None
        if pending:
            try:
                linked = uuid.UUID(str(pending["id"]))
            except (ValueError, TypeError):
                linked = None
        await self._storage.upsert_telemost_recording(
            meeting_id=meeting_id,
            imap_uid=mail.uid,
            message_id=mail.message_id,
            subject=rec.subject or mail.subject,
            video_url=video_url,
            audio_url=audio_url,
            linked_pending_id=linked,
        )
        await self._record_mail_cache(
            imap_uid=mail.uid,
            message_id=mail.message_id,
            status=STATUS_SKIPPED,
            label=f"Запись №{meeting_id}"[:200],
            error_message="Видео-запись (ожидает конспект или шортсы)",
        )
        logger.info(
            "telemost recording stored meeting_id=%s uid=%s url=%s",
            meeting_id,
            mail.uid,
            video_url[:80],
        )

    async def _sync_recent_recording_mails(self) -> None:
        """Подхватывает письма с записью из последних писем ящика."""
        mails: List[FetchedMail] = await asyncio.to_thread(
            self._imap.fetch_recent,
            from_markers=self._from_markers,
            limit=35,
        )
        for mail in mails:
            if self._mail_has_txt_attachment(mail):
                continue
            rec = parse_recording_email(mail.subject, mail.body_text)
            if not rec or (not rec.video_url and not rec.audio_url):
                continue
            await self._ingest_recording_mail(mail, rec)

    @property
    def enabled(self) -> bool:
        return (
            self._imap.configured
            and self._index is not None
            and self._notify_chat_id != 0
            and bool(self._speaker_names)
        )

    async def poll_new_mail(self) -> List[dict[str, Any]]:
        """Новые письма → pending в БД → данные для уведомления в Telegram."""
        if not self._imap.configured:
            return []

        last_uid = await self._storage.get_telemost_mail_last_uid()
        mails: List[FetchedMail] = await asyncio.to_thread(
            self._imap.fetch_new_since_uid,
            min_uid_exclusive=last_uid,
            from_markers=self._from_markers,
            limit=15,
        )
        notifications: List[dict[str, Any]] = []
        max_uid = last_uid

        for mail in mails:
            try:
                uid_int = int(mail.uid)
                max_uid = max(max_uid, uid_int)
            except ValueError:
                pass

            if await self._mail_already_handled(mail):
                continue

            if not self._mail_has_txt_attachment(mail):
                rec = parse_recording_email(mail.subject, mail.body_text)
                if rec and (rec.video_url or rec.audio_url):
                    await self._ingest_recording_mail(mail, rec)
                    continue
                await self._skip_mail_without_attachment(mail)
                continue

            _pid, note = await self._create_pending_from_mail(mail)
            notifications.append(note)

        if max_uid > last_uid:
            await self._storage.set_telemost_mail_last_uid(max_uid)

        await self._sync_recent_recording_mails()

        return notifications

    async def index_pending(self, pending_id: uuid.UUID) -> tuple[int, str]:
        row = await self._storage.get_telemost_mail_pending(pending_id)
        if not row:
            return 0, "Запись не найдена"
        if row.get("status") == "indexed":
            return int(row.get("chunks_count") or 0), "Уже в RAG"
        if row.get("status") == "ignored":
            return 0, "Ранее отклонено"

        clf_raw = row.get("classification") or {}
        classification = TelemostClassification(
            is_club_meeting=bool(clf_raw.get("is_club_meeting")),
            recommend_index=bool(clf_raw.get("recommend_index")),
            title=str(clf_raw.get("title") or ""),
            meeting_topic=str(clf_raw.get("meeting_topic") or ""),
            content_type=str(clf_raw.get("content_type") or ""),
            content_category=str(clf_raw.get("content_category") or "dialog"),
            product=str(clf_raw.get("product") or ""),
            tags=str(clf_raw.get("tags") or ""),
            summary=str(clf_raw.get("summary") or ""),
            admin_note=str(clf_raw.get("admin_note") or ""),
            reason=str(clf_raw.get("reason") or ""),
        )

        transcript = (row.get("transcript_text") or "").strip()
        expert = extract_expert_speech(transcript, self._speaker_names)
        if len(expert.strip()) < 80:
            expert = await extract_expert_speech_llm_fallback(
                transcript, self._speaker_names
            )

        if not expert.strip():
            await self._storage.update_telemost_mail_pending_status(
                pending_id, status="error", error="Не найдены реплики эксперта в TXT"
            )
            await self._record_mail_cache(
                imap_uid=str(row.get("imap_uid") or ""),
                message_id=str(row.get("message_id") or ""),
                status=STATUS_ERROR,
                label=(row.get("subject") or "")[:200],
                error_message="Не найдены реплики эксперта",
            )
            return 0, "В расшифровке не найдены реплики эксперта"

        source_label = classification.title or (row.get("subject") or "Телемост")[:80]
        meta = classification.as_chroma_metadata(source_label=source_label)
        meta["telemost_imap_uid"] = str(row.get("imap_uid") or "")
        extra = row.get("extra_metadata") or {}
        if isinstance(extra, dict):
            if extra.get("meeting_date"):
                meta["date"] = str(extra["meeting_date"])[:32]
            meeting_url = (extra.get("meeting_url") or "").strip()
            if meeting_url:
                from bot.features.rag_source_visibility import (
                    VIS_PRIVATE,
                    apply_source_link_to_metadata,
                )

                apply_source_link_to_metadata(meta, meeting_url, VIS_PRIVATE)

        header = (classification.summary or "").strip()
        full_text = f"{header}\n\n{expert}".strip() if header else expert

        dedupe_salt = f"telemost:{row.get('imap_uid')}:{row.get('message_id')}"
        n, _ = await self._index.add_material_text_async(
            full_text,
            base_metadata=meta,
            source=source_label,
            dedupe_salt=dedupe_salt,
        )

        await self._storage.update_telemost_mail_pending_status(
            pending_id,
            status="indexed",
            chunks_count=n,
        )
        await self._record_mail_cache(
            imap_uid=str(row.get("imap_uid") or ""),
            message_id=str(row.get("message_id") or ""),
            status=STATUS_INDEXED,
            chunks_count=n,
            label=source_label,
        )
        extra = row.get("extra_metadata") or {}
        if isinstance(extra, dict) and extra.get("meeting_id"):
            await self._storage.link_recording_to_pending(
                str(extra["meeting_id"]), pending_id
            )
        if n > 0 and self._bot_app is not None:
            fresh = await self._storage.get_telemost_mail_pending(pending_id)
            if fresh:
                if getattr(config, "TELEMOST_VIDEO_SHORTS_ENABLED", False):
                    from telemost_shorts.pipeline import enqueue_telemost_shorts

                    enqueue_telemost_shorts(self._bot_app, pending_id, fresh, meta)
                if getattr(config, "TELEMOST_AUDIO_CLIPS_ENABLED", False):
                    from telemost_audio.pipeline import enqueue_telemost_audio

                    enqueue_telemost_audio(self._bot_app, pending_id, fresh, meta)
        return n, format_telemost_index_result_html(n, meta)

    async def ignore_pending(self, pending_id: uuid.UUID) -> None:
        row = await self._storage.get_telemost_mail_pending(pending_id)
        await self._storage.update_telemost_mail_pending_status(
            pending_id, status="ignored"
        )
        if row:
            await self._record_mail_cache(
                imap_uid=str(row.get("imap_uid") or ""),
                message_id=str(row.get("message_id") or ""),
                status=STATUS_IGNORED,
                label=(row.get("subject") or "")[:200],
            )

    def _note_from_pending_row(self, row: dict[str, Any]) -> dict[str, Any]:
        clf_raw = row.get("classification") or {}
        extra = row.get("extra_metadata") or {}
        if not isinstance(extra, dict):
            extra = {}
        return {
            "pending_id": str(row.get("id")),
            "subject": row.get("subject") or "",
            "classification": TelemostClassification(
                is_club_meeting=bool(clf_raw.get("is_club_meeting")),
                recommend_index=bool(clf_raw.get("recommend_index")),
                title=str(clf_raw.get("title") or ""),
                meeting_topic=str(clf_raw.get("meeting_topic") or ""),
                content_type=str(clf_raw.get("content_type") or ""),
                content_category=str(clf_raw.get("content_category") or "dialog"),
                product=str(clf_raw.get("product") or ""),
                tags=str(clf_raw.get("tags") or ""),
                summary=str(clf_raw.get("summary") or ""),
                admin_note=str(clf_raw.get("admin_note") or ""),
                reason=str(clf_raw.get("reason") or ""),
            ),
            "has_transcript": bool((row.get("transcript_text") or "").strip()),
            "meeting_id": extra.get("meeting_id"),
            "meeting_date": extra.get("meeting_date"),
            "backfill": True,
        }

    async def _offer_pending_mail(
        self,
        *,
        pending_id: uuid.UUID,
        note: dict[str, Any],
        notify_cb: NotifyCallback,
        stats: BackfillStats,
        decision_timeout_sec: float,
        mail_label: str,
    ) -> None:
        note = {**note, "backfill": True}
        pid_s = str(pending_id)
        register_mail_decision_wait(pid_s)
        await notify_cb(note)
        stats.offered += 1
        outcome = await wait_mail_decision(pid_s, timeout_sec=decision_timeout_sec)
        if outcome == "load":
            n, _msg = await self.index_pending(pending_id)
            if n > 0:
                stats.indexed += 1
                stats.chunks += n
            else:
                stats.errors += 1
                stats.messages.append(mail_label[:120])
        elif outcome == "ignore":
            await self.ignore_pending(pending_id)
            stats.ignored += 1
        else:
            stats.errors += 1
            stats.messages.append(f"Таймаут: {mail_label[:80]}")

    async def backfill_mail(
        self,
        days: int,
        *,
        notify_cb: NotifyCallback,
        decision_timeout_sec: float = 86_400,
    ) -> BackfillStats:
        stats = BackfillStats(source="mail", days=max(1, int(days)))
        if not self._imap.configured:
            stats.messages.append("IMAP не настроен")
            return stats

        since = datetime.now(timezone.utc) - timedelta(days=stats.days)
        mails: List[FetchedMail] = await asyncio.to_thread(
            self._imap.fetch_since_date,
            since,
            from_markers=self._from_markers,
            limit=500,
        )

        for mail in mails:
            stats.scanned += 1
            key = telemost_mail_cache_key(
                imap_uid=mail.uid, message_id=mail.message_id
            )
            if await self._storage.rag_import_cache_should_skip(
                IMPORT_TELEMOST_MAIL, key
            ):
                stats.skipped_cached += 1
                continue

            if not self._mail_has_txt_attachment(mail):
                rec = parse_recording_email(mail.subject, mail.body_text)
                if rec and (rec.video_url or rec.audio_url):
                    if not await self._storage.telemost_recording_imap_uid_exists(
                        mail.uid
                    ):
                        await self._ingest_recording_mail(mail, rec)
                    stats.skipped_cached += 1
                    continue
                stats.skipped_cached += 1
                await self._skip_mail_without_attachment(mail)
                continue

            existing = await self._storage.get_telemost_mail_pending_by_imap_uid(
                mail.uid
            )
            if existing and str(existing.get("status") or "") == "pending":
                if not (existing.get("transcript_text") or "").strip():
                    stats.skipped_cached += 1
                    try:
                        pending_id = uuid.UUID(str(existing["id"]))
                        await self.ignore_pending(pending_id)
                    except Exception as e:
                        logger.warning(
                            "backfill_mail: pending без TXT %s: %s", mail.uid, e
                        )
                    await self._skip_mail_without_attachment(mail)
                    continue
                try:
                    pending_id = uuid.UUID(str(existing["id"]))
                    note = self._note_from_pending_row(existing)
                    await self._offer_pending_mail(
                        pending_id=pending_id,
                        note=note,
                        notify_cb=notify_cb,
                        stats=stats,
                        decision_timeout_sec=decision_timeout_sec,
                        mail_label=(mail.subject or mail.uid),
                    )
                except Exception as e:
                    stats.errors += 1
                    logger.exception("backfill_mail re-offer %s: %s", mail.uid, e)
                continue

            if await self._mail_already_handled(mail):
                stats.skipped_cached += 1
                continue

            try:
                pending_id, note = await self._create_pending_from_mail(mail)
                await self._offer_pending_mail(
                    pending_id=pending_id,
                    note=note,
                    notify_cb=notify_cb,
                    stats=stats,
                    decision_timeout_sec=decision_timeout_sec,
                    mail_label=(mail.subject or mail.uid),
                )
            except Exception as e:
                stats.errors += 1
                logger.exception("backfill_mail %s: %s", mail.uid, e)
                stats.messages.append(f"{mail.subject or mail.uid}: {e}"[:120])
                await self._record_mail_cache(
                    imap_uid=mail.uid,
                    message_id=mail.message_id,
                    status=STATUS_ERROR,
                    label=(mail.subject or "")[:200],
                    error_message=str(e)[:500],
                )

        return stats
