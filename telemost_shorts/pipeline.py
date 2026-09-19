"""Фоновая нарезка шортов после загрузки эфира в RAG."""

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
from telemost_mail.recording_resolver import wait_and_download_recording
from telemost_mail.timestamped_speech import parse_expert_segments
from telemost_shorts.arena_runner import format_clips_for_shorts
from telemost_shorts.ffmpeg_render import render_vertical_clips
from telemost_shorts.moments_llm import ClipMoment, pick_viral_moments

logger = logging.getLogger(__name__)

_active_shorts: set[str] = set()


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


def enqueue_telemost_shorts(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    force: bool = False,
    regenerate_moments: bool = False,
) -> bool:
    if not getattr(config, "TELEMOST_SHORTS_ENABLED", False):
        return False
    if not getattr(config, "TELEMOST_VIDEO_SHORTS_ENABLED", False):
        return False
    pid = str(pending_id)
    if pid in _active_shorts and not force:
        logger.info("telemost_shorts: already running pending_id=%s", pid)
        return False
    run_id = uuid.uuid4().hex[:8] if regenerate_moments else pid
    _active_shorts.add(run_id)
    asyncio.create_task(
        _run_shorts_pipeline(
            bot_app,
            pending_id,
            row,
            meta,
            regenerate_moments=regenerate_moments,
            run_id=run_id,
        ),
        name=f"telemost_shorts_{run_id[:8]}",
    )
    return True


async def enqueue_telemost_shorts_last(
    bot_app: Any,
    *,
    force: bool = True,
    regenerate_moments: bool = True,
) -> tuple[bool, str]:
    """Принудительная нарезка последнего эфира, загруженного в RAG."""
    storage = getattr(bot_app, "user_storage", None)
    if storage is None:
        return False, "Хранилище недоступно"
    row = await storage.get_last_indexed_telemost_mail()
    if not row:
        return False, "Нет проиндексированных эфиров Телемоста"
    try:
        pending_id = uuid.UUID(str(row["id"]))
    except (ValueError, TypeError):
        return False, "Некорректный pending_id последнего эфира"
    meta = _build_chroma_meta_from_row(row)
    title = meta.get("topic_title") or meta.get("source") or row.get("subject") or "Эфир"
    if not enqueue_telemost_shorts(
        bot_app,
        pending_id,
        row,
        meta,
        force=force,
        regenerate_moments=regenerate_moments,
    ):
        return False, "Нарезка уже выполняется для этого эфира"
    return True, str(title)[:120]


async def _resolve_video_path(
    row: Dict[str, Any],
    imap: YandexImapClient,
    *,
    storage,
    bot: Any,
    chat_id: int,
    topic_id: Optional[int],
) -> Optional[str]:
    extra = row.get("extra_metadata") or {}
    meeting_id = ""
    if isinstance(extra, dict):
        meeting_id = (extra.get("meeting_id") or "").strip()
        path = (extra.get("video_local_path") or "").strip()
        if path and Path(path).is_file():
            return path

    if not meeting_id:
        return None

    async def notify(text: str) -> None:
        if bot and chat_id:
            await bot.send_message(
                chat_id,
                text,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
            )

    return await wait_and_download_recording(
        meeting_id,
        storage=storage,
        imap=imap,
        notify=notify,
    )


async def _run_shorts_pipeline(
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
            logger.warning("telemost_shorts: no chat/bot")
            return

        prefix = "🎬 <b>Шортсы</b>"
        if regenerate_moments:
            prefix = "🎬 <b>Новая нарезка</b>"
        await bot.send_message(
            chat_id,
            f"{prefix}: подбираю другие моменты для «{html_escape(str(title)[:120])}»…",
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
                "⚠️ Шортсы: в TXT нет таймкодов речи эксперта.",
                message_thread_id=topic_id,
            )
            return

        imap = YandexImapClient(
            getattr(config, "TELEMOST_MAIL_LOGIN", "") or "",
            getattr(config, "TELEMOST_MAIL_PASSWORD", "") or "",
            host=getattr(config, "TELEMOST_MAIL_IMAP_HOST", "imap.yandex.ru"),
            port=int(getattr(config, "TELEMOST_MAIL_IMAP_PORT", 993) or 993),
            folder=getattr(config, "TELEMOST_MAIL_FOLDER", "INBOX") or "INBOX",
        )
        video_path = await _resolve_video_path(
            row,
            imap,
            storage=storage,
            bot=bot,
            chat_id=chat_id,
            topic_id=topic_id,
        )
        if not video_path:
            extra = row.get("extra_metadata") or {}
            mid = (extra.get("meeting_id") if isinstance(extra, dict) else "") or "?"
            await bot.send_message(
                chat_id,
                f"⚠️ Шортсы: не удалось получить запись встречи №<code>{html_escape(str(mid))}</code>. "
                "Проверьте, что пришло письмо «Запись встречи» со ссылкой на видео.",
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
            )
            return

        count = int(getattr(config, "TELEMOST_SHORTS_COUNT", 5) or 5)
        max_dur = int(getattr(config, "TELEMOST_SHORTS_MAX_DURATION_SEC", 60) or 60)
        philosophy = getattr(config, "TELEMOST_SHORTS_PHILOSOPHY_HINT", "") or ""

        moments: List[ClipMoment] = await pick_viral_moments(
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
                "⚠️ Шортсы: не удалось выбрать моменты.",
                message_thread_id=topic_id,
            )
            return

        work_root = Path(getattr(config, "TELEMOST_SHORTS_WORK_DIR", "data/telemost_shorts"))
        run_suffix = pid[:8]
        if regenerate_moments:
            run_suffix = f"{pid[:8]}_{int(time.time())}"
        work_dir = work_root / run_suffix
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        clips = await render_vertical_clips(
            video_path,
            moments,
            segments,
            work_dir=work_dir / "raw",
            max_duration_sec=max_dur,
        )
        if getattr(config, "TELEMOST_SHORTS_USE_ARENA", False):
            clips = await format_clips_for_shorts(
                clips,
                work_dir=work_dir / "arena",
                platform="youtube-shorts",
            )

        if not clips:
            await bot.send_message(
                chat_id,
                "❌ Шортсы: ffmpeg не смог собрать клипы (см. лог).",
                message_thread_id=topic_id,
            )
            return

        sent = 0
        for i, (clip_path, moment) in enumerate(zip(clips, moments), start=1):
            cap = (
                f"<b>Short {i}/{len(clips)}</b> · {html_escape(moment.title)}\n"
                f"{html_escape(moment.hook)}\n"
                f"<i>{html_escape(moment.reason[:200])}</i>"
            )
            try:
                await bot.send_video(
                    chat_id,
                    FSInputFile(str(clip_path)),
                    caption=cap,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=topic_id,
                    supports_streaming=True,
                )
                sent += 1
                await asyncio.sleep(0.8)
            except Exception as e:
                logger.error("send short %s: %s", clip_path, e)

        await bot.send_message(
            chat_id,
            f"✅ <b>Шортсы готовы</b>: {sent} из {len(moments)} "
            f"(вертикаль 9:16, до {max_dur} с, субтитры).\n"
            f"Источник: {html_escape(str(title)[:120])}",
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )
    except Exception as e:
        logger.exception("telemost_shorts pipeline pending=%s: %s", pid, e)
        if bot and chat_id:
            try:
                await bot.send_message(
                    chat_id,
                    f"❌ Ошибка нарезки шортсов: {html_escape(str(e)[:400])}",
                    parse_mode=ParseMode.HTML,
                    message_thread_id=topic_id,
                )
            except Exception:
                pass
    finally:
        _active_shorts.discard(active_key)
