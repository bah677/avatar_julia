"""Фоновая нарезка аудио-мини-подкастов после загрузки эфира в RAG."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from config import config
from telemost_mail.classifier_llm import TelemostClassification
from telemost_mail.imap_client import YandexImapClient
from telemost_mail.timestamped_speech import parse_expert_segments
from telemost_audio.ffmpeg_render import render_audio_clips
from telemost_audio.moments_llm import AudioClipMoment, pick_audio_moments
from telemost_audio.recording_resolver import wait_and_download_audio

logger = logging.getLogger(__name__)

_active_audio: set[str] = set()


def _shorts_chat_topic() -> tuple[int, Optional[int]]:
    chat = int(config.rag_shorts_chat_id or 0)
    topic = int(getattr(config, "RAG_SHORTS_TOPIC_ID", 0) or 0)
    return chat, topic or None


def _build_chroma_meta_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
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
    source_label = classification.title or (row.get("subject") or "Телемост")[:80]
    meta = classification.as_chroma_metadata(source_label=source_label)
    extra = row.get("extra_metadata") or {}
    if isinstance(extra, dict) and extra.get("meeting_date"):
        meta["date"] = str(extra["meeting_date"])[:32]
    return meta


def enqueue_telemost_audio(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    force: bool = False,
    regenerate_moments: bool = False,
) -> bool:
    if not getattr(config, "TELEMOST_AUDIO_CLIPS_ENABLED", False):
        return False
    pid = str(pending_id)
    if pid in _active_audio and not force:
        logger.info("telemost_audio: already running pending_id=%s", pid)
        return False
    run_id = uuid.uuid4().hex[:8] if regenerate_moments else pid
    _active_audio.add(run_id)
    asyncio.create_task(
        _run_audio_pipeline(
            bot_app,
            pending_id,
            row,
            meta,
            regenerate_moments=regenerate_moments,
            run_id=run_id,
        ),
        name=f"telemost_audio_{run_id[:8]}",
    )
    return True


async def enqueue_telemost_audio_last(
    bot_app: Any,
    *,
    force: bool = True,
    regenerate_moments: bool = True,
) -> tuple[bool, str]:
    storage = getattr(bot_app, "user_storage", None)
    if storage is None:
        return False, "Хранилище недоступно"
    row = await storage.get_last_indexed_telemost_mail()
    if not row:
        return False, "Нет проиндексированных эфиров Телемоста"
    try:
        pending_id = uuid.UUID(str(row["id"]))
    except (ValueError, TypeError):
        return False, "Некорректный pending_id"
    meta = _build_chroma_meta_from_row(row)
    title = meta.get("topic_title") or meta.get("source") or row.get("subject") or "Эфир"
    if not enqueue_telemost_audio(
        bot_app,
        pending_id,
        row,
        meta,
        force=force,
        regenerate_moments=regenerate_moments,
    ):
        return False, "Аудио-нарезка уже выполняется"
    return True, str(title)[:120]


async def _run_audio_pipeline(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    regenerate_moments: bool = False,
    run_id: str = "",
) -> None:
    pid = str(pending_id)
    active_key = run_id or pid
    chat_id, topic_id = _shorts_chat_topic()
    bot = getattr(bot_app, "bot", None)
    storage = getattr(bot_app, "user_storage", None)
    title = (
        meta.get("topic_title")
        or meta.get("source")
        or row.get("subject")
        or "Эфир"
    )

    try:
        if not chat_id or not bot:
            logger.warning("telemost_audio: no chat/bot")
            return

        prefix = "🎙 <b>Аудио</b>"
        if regenerate_moments:
            prefix = "🎙 <b>Новая аудио-нарезка</b>"
        await bot.send_message(
            chat_id,
            f"{prefix}: подбираю фрагменты (~1 мин) для «{html_escape(str(title)[:120])}»…",
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )

        speakers = [
            s.strip()
            for s in (getattr(config, "TELEMOST_MAIL_AVATAR_SPEAKER_NAMES", "") or "").split(",")
            if s.strip()
        ]
        transcript = (row.get("transcript_text") or "").strip()
        segments = parse_expert_segments(transcript, speakers)
        if not segments:
            await bot.send_message(
                chat_id,
                "⚠️ Аудио: в TXT нет таймкодов речи эксперта.",
                message_thread_id=topic_id,
            )
            return

        extra = row.get("extra_metadata") or {}
        meeting_id = (extra.get("meeting_id") if isinstance(extra, dict) else "") or ""

        imap = YandexImapClient(
            getattr(config, "TELEMOST_MAIL_LOGIN", "") or "",
            getattr(config, "TELEMOST_MAIL_PASSWORD", "") or "",
            host=getattr(config, "TELEMOST_MAIL_IMAP_HOST", "imap.yandex.ru"),
            port=int(getattr(config, "TELEMOST_MAIL_IMAP_PORT", 993) or 993),
            folder=getattr(config, "TELEMOST_MAIL_FOLDER", "INBOX") or "INBOX",
        )

        async def notify(text: str) -> None:
            await bot.send_message(
                chat_id,
                text,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
            )

        audio_path = await wait_and_download_audio(
            meeting_id,
            storage=storage,
            imap=imap,
            notify=notify,
        )
        if not audio_path:
            await bot.send_message(
                chat_id,
                f"⚠️ Аудио: не удалось получить запись встречи №<code>{html_escape(str(meeting_id))}</code>.",
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
            )
            return

        count = int(getattr(config, "TELEMOST_AUDIO_CLIPS_COUNT", 5) or 5)
        max_dur = int(getattr(config, "TELEMOST_AUDIO_CLIPS_MAX_DURATION_SEC", 60) or 60)
        philosophy = getattr(config, "TELEMOST_SHORTS_PHILOSOPHY_HINT", "") or ""

        moments: List[AudioClipMoment] = await pick_audio_moments(
            segments,
            philosophy_hint=philosophy,
            meeting_title=str(title),
            count=count,
            max_duration_sec=max_dur,
            regenerate=regenerate_moments,
        )
        if not moments:
            await bot.send_message(
                chat_id,
                "⚠️ Аудио: не удалось выбрать фрагменты.",
                message_thread_id=topic_id,
            )
            return

        work_root = Path(
            getattr(config, "TELEMOST_AUDIO_WORK_DIR", "data/telemost_audio_clips")
        )
        run_suffix = pid[:8]
        if regenerate_moments:
            run_suffix = f"{pid[:8]}_{int(time.time())}"
        work_dir = work_root / run_suffix
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        clips = await render_audio_clips(
            audio_path,
            moments,
            work_dir=work_dir / "voices",
            max_duration_sec=max_dur,
        )
        if not clips:
            await bot.send_message(
                chat_id,
                "❌ Аудио: ffmpeg не смог нарезать фрагменты.",
                message_thread_id=topic_id,
            )
            return

        sent = 0
        for i, (clip_path, moment) in enumerate(zip(clips, moments), start=1):
            caption = (moment.hook or moment.title or "Фрагмент эфира")[:1024]
            try:
                await bot.send_voice(
                    chat_id,
                    FSInputFile(str(clip_path)),
                    caption=caption,
                    message_thread_id=topic_id,
                )
                sent += 1
                await asyncio.sleep(0.6)
            except Exception as e:
                logger.error("send voice %s: %s", clip_path, e)

        await bot.send_message(
            chat_id,
            f"✅ <b>Аудио готово</b>: {sent} голосовых (~1 мин каждое).\n"
            f"Источник: {html_escape(str(title)[:120])}",
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )
    except Exception as e:
        logger.exception("telemost_audio pipeline pending=%s: %s", pid, e)
        if bot and chat_id:
            try:
                await bot.send_message(
                    chat_id,
                    f"❌ Ошибка аудио-нарезки: {html_escape(str(e)[:400])}",
                    parse_mode=ParseMode.HTML,
                    message_thread_id=topic_id,
                )
            except Exception:
                pass
    finally:
        _active_audio.discard(active_key)
