"""
Индексация материалов для RAG из групп (супергруппы, форумы) и каналов.

Поддерживает ``message`` (группы) и ``channel_post`` (каналы).
Тонкий слой над ``rag.MaterialIndexService`` + медиапроцессор.
Не импортирует ``rag/`` напрямую в Telegram-хендлерах — только через ``RagStack``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from html import escape as html_escape
from typing import Any, Dict, List, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ContentType, ParseMode
from aiogram.exceptions import TelegramNotFound, TelegramRetryAfter
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.media_processing.models import MediaType, ProcessedMedia
from bot.features.forum_topic_name_cache import (
    debug_cache_snapshot as forum_topic_cache_snapshot,
    get_cached_forum_topic_name,
    remember_forum_topic_name,
)
from bot.features.rag_group_metadata import (
    build_source_identifier,
    infer_dialog_role,
    message_date_iso_utc,
    message_in_rag_groups_scope,
    resolve_content_type_product_category,
    telegram_internal_message_link,
    testimonial_metadata_overrides,
)
from bot.features.rag_source_visibility import (
    SOURCE_TELEGRAM_GROUP,
    apply_source_link_to_metadata,
    resolve_source_visibility,
    youtube_public_link,
)
from bot.media_processing.processors.youtube import (
    extract_youtube_urls,
    download_youtube_audio,
    transcribe_youtube_audio,
)
from bot.media_processing.config.settings import MEDIA_LIMITS

logger = logging.getLogger(__name__)


def _describe_rag_media_processing_issue(
    processed: ProcessedMedia,
    *,
    has_file_media: bool,
) -> Optional[str]:
    t = (processed.text or "").strip()
    meta = processed.metadata or {}
    err = str(meta.get("error") or "").strip()

    if "[не удалось скачать файл]" in t or meta.get("error") == "download_failed":
        sz = meta.get("file_size")
        extra = f" Размер в апдейте: {sz} байт." if sz is not None else ""
        return (
            "Файл не скачан из Telegram."
            + extra
            + " Частая причина — лимит Bot API на скачивание (~20 MB) или сеть."
        )
    if "[файл слишком большой для обработки]" in t or err == "file_too_large":
        return "Файл больше локального лимита медиапроцессора (GLOBAL_LIMITS max_file_size_bytes)."
    if err == "duration_limit_exceeded":
        return "Превышена максимальная длительность медиа для обработки."
    if "[ошибка обработки]" in t:
        return "Внутренняя ошибка медиапроцессора (см. stack trace в логах)."
    if has_file_media and processed.media_type in (
        MediaType.VOICE,
        MediaType.AUDIO,
        MediaType.VIDEO,
        MediaType.VIDEO_NOTE,
    ):
        if not processed.has_text and (
            "не распознана" in t
            or "без речи" in t
            or "ошибка распознавания" in t
            or "превышен лимит" in t
        ):
            return "Аудио/видео не дало распознанного текста (Whisper/обработка)."
    return None


def _format_rag_index_success_reply(
    message: Message,
    *,
    n_chunks: int,
    topic_title: str,
    content_type: str,
    product_value: str,
    content_category: str,
    group_link: str,
) -> str:
    lines = [
        f"✅ <b>{n_chunks}</b> чанков в RAG",
        f"📌 Топик: <b>{html_escape((topic_title or '')[:220])}</b>",
        f"🏷 Тип: <code>{html_escape((content_type or '')[:120])}</code>",
        f"📦 Продукт: <code>{html_escape((product_value or '')[:120])}</code>",
        f"📂 Вид: <code>{html_escape(content_category or '')}</code>",
    ]
    tid = message.message_thread_id
    if tid is not None:
        lines.append(f"🧵 thread_id: <code>{tid}</code>")
    gl = (group_link or "").strip()
    if gl:
        ge = html_escape(gl, quote=True)
        lines.append(f'🔗 <a href="{ge}">сообщение</a>')
    return "\n".join(lines)

# Запас под обёртку <pre> и заголовок (лимит сообщения Telegram — 4096).
_DEBUG_PRE_MAX = 3600

# Антифлуд: последовательные ответы индексатора в группу + пауза при TelegramRetryAfter.
_RAG_REPLY_LOCK = asyncio.Lock()
_RAG_LAST_REPLY_MONO = 0.0
_RAG_GROUP_REPLY_SPACING_SEC = 0.55
_RAG_GROUP_REPLY_MAX_RETRIES = 12


async def _rag_throttled_reply(message: Message, text: str, **kwargs: Any) -> None:
    """Пауза между SendMessage из индексатора и повтор после flood limit."""
    global _RAG_LAST_REPLY_MONO
    loop = asyncio.get_running_loop()
    async with _RAG_REPLY_LOCK:
        wait_gap = _RAG_GROUP_REPLY_SPACING_SEC - (loop.time() - _RAG_LAST_REPLY_MONO)
        if wait_gap > 0:
            await asyncio.sleep(wait_gap)

        attempt = 0
        while attempt < _RAG_GROUP_REPLY_MAX_RETRIES:
            try:
                await message.reply(text, **kwargs)
                break
            except TelegramRetryAfter as e:
                wait = float(getattr(e, "retry_after", None) or 5) + 0.35
                logger.warning(
                    "group_rag_indexer: flood control Telegram, ждём %.1f с (%s/%s)",
                    wait,
                    attempt + 1,
                    _RAG_GROUP_REPLY_MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                attempt += 1
            except Exception:
                logger.exception("group_rag_indexer: не удалось отправить reply")
                break
        _RAG_LAST_REPLY_MONO = loop.time()

# Сервисные сообщения форума / чата — не кормим медиапроцессору и не шлём «нет текста».
_SKIP_RAG_INDEX_CONTENT_TYPES = frozenset(
    {
        ContentType.FORUM_TOPIC_CREATED,
        ContentType.FORUM_TOPIC_EDITED,
        ContentType.FORUM_TOPIC_CLOSED,
        ContentType.FORUM_TOPIC_REOPENED,
        ContentType.NEW_CHAT_TITLE,
        ContentType.GENERAL_FORUM_TOPIC_HIDDEN,
        ContentType.GENERAL_FORUM_TOPIC_UNHIDDEN,
    }
)


def _message_looks_like_bot_command(message: Message) -> bool:
    """Команды бота в группе — отдельные хендлеры (/rag_backfill и т.п.)."""
    text = (message.text or message.caption or "").strip()
    if not text.startswith("/"):
        return False
    return bool(re.match(r"^/\w+(@\w+)?", text))


class GroupRagIndexerFeature(BaseFeature):
    """Сообщения из RAG-групп → чанки в Chroma; короткий reply (подробный дамп только в verbose)."""

    name = "group_rag_indexer"

    def __init__(self) -> None:
        super().__init__()
        self._app: Optional[TelegramBotApp] = None
        self._groups_map: dict[int, Optional[frozenset[int]]] = {}
        self._testimonial_groups_map: dict[int, Optional[frozenset[int]]] = {}

    async def _rag_group_reply(self, message: Message, text: str, **kwargs: Any) -> None:
        """Реплай в RAG-группу; при RAG_GROUP_INDEX_REPLIES=off — только лог (тихий режим)."""
        if not self.config.RAG_GROUP_INDEX_REPLIES:
            logger.info(
                "[%s] тихий режим: ответ в группу не отправляем: %s",
                self.name,
                text[:500],
            )
            return
        await _rag_throttled_reply(message, text, **kwargs)

    def set_bot(self, app: Any) -> None:
        """``TelegramBotApp`` — даёт ``bot``, ``media_processor``, ``rag_stack``."""
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        self._groups_map = dict(self.config.rag_groups_map)
        self._testimonial_groups_map = dict(self.config.rag_testimonial_groups_map)
        index_map = dict(self.config.rag_index_groups_map)
        if not index_map:
            self.log("RAG-группы не заданы (ни RAG_GROUPS, ни RAG_GROUP_CHAT_ID) — хендлер не регистрируется")
            return

        gids = list(index_map.keys())
        chat_filter = F.chat.id.in_(gids)

        dispatcher.message.register(
            self._on_forum_topic_created,
            chat_filter,
            F.content_type == ContentType.FORUM_TOPIC_CREATED,
        )
        dispatcher.message.register(
            self._on_forum_topic_edited,
            chat_filter,
            F.content_type == ContentType.FORUM_TOPIC_EDITED,
        )
        dispatcher.message.register(
            self._on_new_chat_title_debug,
            chat_filter,
            F.content_type == ContentType.NEW_CHAT_TITLE,
        )
        dispatcher.message.register(
            self._on_group_message,
            chat_filter,
            F.func(lambda m: not _message_looks_like_bot_command(m)),
        )
        dispatcher.channel_post.register(
            self._on_channel_post,
            chat_filter,
        )
        for gid, topics in index_map.items():
            t_desc = f"топики: {sorted(topics)}" if topics else "все топики"
            expert = "да" if gid in self._groups_map else "нет"
            testim = "да" if gid in self._testimonial_groups_map else "нет"
            self.log(
                f"Индексация RAG для chat_id={gid} ({t_desc}); expert={expert} testimonial={testim}"
            )
        if self.config.rag_indexer_verbose:
            self.log(
                "rag_indexer_verbose: подробные логи индекса/топика/парсера (RAG_INDEXER_DEBUG=1 или LOG_LEVEL=DEBUG)",
                level="warning",
            )

    async def _resolve_topic_title(self, message: Message) -> str:
        tid = message.message_thread_id
        if not tid:
            if self._app:
                storage = self._app.user_storage
                default = await get_cached_forum_topic_name(
                    storage, message.chat.id, 0
                )
                if default:
                    return default
            return "General"
        if not self._app:
            return f"topic_{tid}"

        logger.debug(
            "[%s] resolve_topic_title: chat_id=%s message_thread_id=%s "
            "is_topic_message=%s content_type=%s",
            self.name,
            message.chat.id,
            tid,
            message.is_topic_message,
            message.content_type,
        )

        storage = self._app.user_storage

        async def _from_cache_or_stub(reason: str) -> str:
            cached = await get_cached_forum_topic_name(
                storage, message.chat.id, tid
            )
            if cached:
                logger.info(
                    "[%s] имя топика из кэша (forum_topic_created/edited), т.к. %s: %r",
                    self.name,
                    reason,
                    cached,
                )
                return cached
            snap = await forum_topic_cache_snapshot(storage, message.chat.id)
            logger.warning(
                "[%s] имя топика недоступно (%s). chat_id=%s thread_id=%s; "
                "getForumTopic часто даёт Not Found без права «управление темами» у бота. "
                "В СУБД нет строки для этого топика; создайте топик заново или переименуйте его "
                "при работающем боте — имя запишется в forum_topic_names. Записей по этому чату: %s.",
                self.name,
                reason,
                message.chat.id,
                tid,
                len(snap),
            )
            if snap:
                logger.debug(
                    "[%s] forum_topic_names keys (chat): %s",
                    self.name,
                    list(snap.keys()),
                )
            return f"topic_{tid}"

        try:
            try:
                from aiogram.methods import GetForumTopic as GetForumTopicCall
            except ImportError:
                from bot.features.get_forum_topic_method import GetForumTopic as GetForumTopicCall

            logger.debug(
                "[%s] вызов getForumTopic chat_id=%s message_thread_id=%s",
                self.name,
                message.chat.id,
                tid,
            )
            res = await self._app.bot(
                GetForumTopicCall(
                    chat_id=message.chat.id,
                    message_thread_id=tid,
                )
            )
            name = getattr(res, "name", None) or getattr(res, "title", None)
            resolved = (name or "").strip()
            if resolved:
                logger.info(
                    "[%s] getForumTopic OK: thread_id=%s name=%r",
                    self.name,
                    tid,
                    resolved,
                )
                try:
                    existing = await get_cached_forum_topic_name(
                        self._app.user_storage, message.chat.id, tid
                    )
                    if existing != resolved:
                        await remember_forum_topic_name(
                            self._app.user_storage,
                            message.chat.id,
                            tid,
                            resolved,
                        )
                except Exception as sync_e:
                    logger.warning(
                        "[%s] forum_topic_names sync после getForumTopic: %s",
                        self.name,
                        sync_e,
                        exc_info=True,
                    )
                return resolved
            return await _from_cache_or_stub("getForumTopic вернул пустое имя")
        except TelegramNotFound as e:
            # Частый случай у api.telegram.org даже при can_manage_topics — не считаем ошибкой процесса.
            logger.info(
                "[%s] getForumTopic: Not Found, chat_id=%s thread_id=%s (%s) — имя берём из кэша при наличии",
                self.name,
                message.chat.id,
                tid,
                str(e).strip(),
            )
            return await _from_cache_or_stub(f"getForumTopic: TelegramNotFound: {e}")
        except Exception as e:
            exc_name = type(e).__name__
            detail = str(e).strip() or repr(e)
            logger.warning(
                "[%s] getForumTopic исключение: %s: %s (chat_id=%s thread_id=%s)",
                self.name,
                exc_name,
                detail,
                message.chat.id,
                tid,
                exc_info=self.config.rag_indexer_verbose,
            )
            return await _from_cache_or_stub(f"getForumTopic: {exc_name}: {detail}")

    async def _on_forum_topic_created(self, message: Message) -> None:
        ftc = message.forum_topic_created
        tid = message.message_thread_id
        if not ftc or not tid:
            logger.debug(
                "[%s] forum_topic_created: пропуск (нет объекта или thread_id)",
                self.name,
            )
            return
        if self.config.rag_indexer_verbose:
            logger.debug(
                "[%s] forum_topic_created: chat_id=%s thread_id=%s name=%r icon_color=%s "
                "is_name_implicit=%s msg_id=%s",
                self.name,
                message.chat.id,
                tid,
                ftc.name,
                getattr(ftc, "icon_color", None),
                getattr(ftc, "is_name_implicit", None),
                message.message_id,
            )
        await remember_forum_topic_name(
            self._app.user_storage, message.chat.id, tid, ftc.name
        )

    async def _on_forum_topic_edited(self, message: Message) -> None:
        fte = message.forum_topic_edited
        tid = message.message_thread_id
        if not fte or not tid:
            return
        if not (fte.name or "").strip():
            logger.info(
                "[%s] forum_topic_edited: кэш имён НЕ обновлён — в апдейте нет поля name "
                "(часто при смене только иконки). thread_id=%s msg_id=%s icon_custom_emoji_id=%r",
                self.name,
                tid,
                message.message_id,
                getattr(fte, "icon_custom_emoji_id", None),
            )
            return
        if self.config.rag_indexer_verbose:
            logger.debug(
                "[%s] forum_topic_edited: chat_id=%s thread_id=%s new_name=%r msg_id=%s",
                self.name,
                message.chat.id,
                tid,
                fte.name,
                message.message_id,
            )
        await remember_forum_topic_name(
            self._app.user_storage, message.chat.id, tid, fte.name
        )

    async def _on_new_chat_title_debug(self, message: Message) -> None:
        """Сервисное сообщение о смене названия группы (не топика)."""
        if not self.config.rag_indexer_verbose:
            return
        nt = message.new_chat_title
        ch = message.chat
        logger.warning(
            "[%s] DEBUG new_chat_title: new_chat_title=%r chat.id=%s chat.type=%s "
            "chat.title(после)=%r username=%s msg_id=%s thread_id=%s",
            self.name,
            nt,
            ch.id,
            ch.type,
            getattr(ch, "title", None),
            getattr(ch, "username", None),
            message.message_id,
            message.message_thread_id,
        )

    async def _on_group_message(self, message: Message) -> None:
        if not message.from_user:
            return
        uid = message.from_user.id
        await self._index_message(message, uid=uid)

    async def _on_channel_post(self, message: Message) -> None:
        """Индексация постов из каналов (channel_post). У них нет from_user."""
        await self._index_message(message, uid=0)

    async def _process_youtube_urls(
        self,
        urls: list[str],
        uid: int,
        message: Message,
    ) -> str:
        """Скачивает и транскрибирует YouTube-видео, возвращает объединённый текст."""
        max_dur = MEDIA_LIMITS.get("youtube", {}).get("max_duration_sec", 4 * 3600)
        openai_client = self._app.openai_client
        parts: list[str] = []

        for url in urls:
            logger.info("[%s] YouTube: скачиваем аудио %s", self.name, url)
            await self._rag_group_reply(
                message,
                f"🎬 <b>YouTube</b>: скачиваю аудио…\n<code>{html_escape(url[:200])}</code>",
                reply_to_message_id=message.message_id,
                parse_mode=ParseMode.HTML,
            )

            audio_path: str | None = None
            try:
                audio_path = await download_youtube_audio(
                    url, max_duration_sec=max_dur
                )
                if not audio_path:
                    logger.warning("[%s] YouTube: не удалось скачать %s", self.name, url)
                    await self._rag_group_reply(
                        message,
                        f"⚠️ <b>YouTube</b>: не удалось скачать аудио\n<code>{html_escape(url[:200])}</code>",
                        reply_to_message_id=message.message_id,
                        parse_mode=ParseMode.HTML,
                    )
                    continue

                logger.info("[%s] YouTube: транскрибируем %s", self.name, url)
                text = await transcribe_youtube_audio(
                    audio_path, openai_client, uid
                )
                if text and text.strip():
                    parts.append(f"[YouTube {url}]\n{text.strip()}")
                    logger.info(
                        "[%s] YouTube OK: %s символов из %s",
                        self.name, len(text), url,
                    )
                else:
                    logger.warning("[%s] YouTube: нет речи в %s", self.name, url)

            except Exception as e:
                logger.error("[%s] YouTube error %s: %s", self.name, url, e, exc_info=True)
                await self._rag_group_reply(
                    message,
                    f"❌ <b>YouTube</b>: ошибка обработки\n<pre>{html_escape(str(e)[:400])}</pre>",
                    reply_to_message_id=message.message_id,
                    parse_mode=ParseMode.HTML,
                )
            finally:
                if audio_path:
                    try:
                        os.unlink(audio_path)
                        parent = os.path.dirname(audio_path)
                        if parent and not os.listdir(parent):
                            os.rmdir(parent)
                    except OSError:
                        pass

        return "\n\n".join(parts)

    async def _index_message(self, message: Message, *, uid: int) -> None:
        """Общая логика индексации — вызывается и для групп, и для каналов."""
        assert self._app is not None

        rs = self._app.rag_stack
        if rs is None:
            logger.debug("[%s] rag_stack выключен — пропуск", self.name)
            return

        tid = message.message_thread_id
        if tid is not None and int(tid) in self.config.rag_exclude_topic_ids:
            logger.debug(
                "[%s] топик %s в RAG_EXCLUDE_TOPIC_IDS — пропуск индексации",
                self.name,
                tid,
            )
            return

        in_testimonial = message_in_rag_groups_scope(
            message, self._testimonial_groups_map
        )
        in_expert = message_in_rag_groups_scope(message, self._groups_map)
        if not in_testimonial and not in_expert:
            logger.debug(
                "[%s] вне scope RAG (expert/testimonial), chat_id=%s thread_id=%s",
                self.name,
                message.chat.id,
                message.message_thread_id,
            )
            return
        is_testimonial_chunk = in_testimonial

        if self.config.rag_indexer_verbose:
            ch = message.chat
            logger.debug(
                "[%s] DEBUG входящее сообщение: msg_id=%s thread_id=%s is_topic_message=%s "
                "content_type=%s chat.id=%s chat.type=%s chat.title=%r username=%s "
                "from_user.id=%s caption_len=%s text_len=%s has_photo=%s has_document=%s",
                self.name,
                message.message_id,
                message.message_thread_id,
                message.is_topic_message,
                message.content_type,
                ch.id,
                ch.type,
                getattr(ch, "title", None),
                getattr(ch, "username", None),
                uid or None,
                len(message.caption or ""),
                len(message.text or ""),
                bool(message.photo),
                bool(message.document),
            )

        if message.content_type in _SKIP_RAG_INDEX_CONTENT_TYPES:
            if self.config.rag_indexer_verbose:
                logger.debug(
                    "[%s] индексация пропущена (сервисное сообщение): %s",
                    self.name,
                    message.content_type,
                )
            return

        mp = self._app.media_processor

        has_file_media = bool(
            message.document
            or message.photo
            or message.video
            or message.voice
            or message.audio
            or message.video_note
        )

        try:
            processed = await mp.process_message(message, uid, None)
        except Exception as e:
            logger.error("[%s] media: %s", self.name, e, exc_info=True)
            err_html = (
                "❌ <b>Ошибка обработки</b>\n"
                f"<pre>{html_escape(str(e)[:900])}</pre>"
            )
            await self._rag_group_reply(
                message,
                err_html,
                reply_to_message_id=message.message_id,
                parse_mode=ParseMode.HTML,
            )
            return

        from bot.handlers.messages import text_for_feature_route

        raw_text = text_for_feature_route(processed, message)

        yt_urls = extract_youtube_urls(raw_text)
        if yt_urls:
            yt_transcription = await self._process_youtube_urls(
                yt_urls, uid, message
            )
            if yt_transcription:
                raw_text = f"{raw_text}\n\n{yt_transcription}"
                has_file_media = True

        media_issue = _describe_rag_media_processing_issue(
            processed, has_file_media=has_file_media
        )
        if media_issue:
            logger.warning("[%s] медиа не готово к индексации: %s", self.name, media_issue)
            warn_html = (
                "⚠️ <b>Не в RAG</b>\n"
                f"{html_escape(media_issue)}\n"
                "<i>Подробности в логах сервера.</i>"
            )
            await self._rag_group_reply(
                message,
                warn_html,
                reply_to_message_id=message.message_id,
                parse_mode=ParseMode.HTML,
            )
            return

        min_len = int(self.config.RAG_MIN_INDEX_CHARS or 300)
        if not raw_text.strip():
            if has_file_media:
                logger.warning("[%s] после обработки медиа нет текста", self.name)
            await self._rag_group_reply(
                message,
                "⚠️ <b>Нет текста</b> для индексации после обработки.",
                reply_to_message_id=message.message_id,
                parse_mode=ParseMode.HTML,
            )
            return

        if (
            message.new_chat_members is not None
            or message.left_chat_member is not None
            or message.pinned_message is not None
        ):
            return

        if not has_file_media and len(raw_text.strip()) < min_len:
            logger.debug(
                "[%s] короткий текст без файла (%s < %s) — пропуск",
                self.name,
                len(raw_text.strip()),
                min_len,
            )
            return

        topic_title = await self._resolve_topic_title(message)
        content_type, product_value, content_category = resolve_content_type_product_category(
            topic_title
        )
        tags = await extract_content_tags(raw_text)
        source_label = build_source_identifier(message, raw_text, has_file_media)
        date_iso = message_date_iso_utc(message)
        group_link = telegram_internal_message_link(message)
        chat_label = (message.chat.title or "").strip() or f"chat {message.chat.id}"

        if self.config.rag_indexer_verbose:
            snap = await forum_topic_cache_snapshot(
                self._app.user_storage, message.chat.id
            )
            key = (message.chat.id, message.message_thread_id)
            logger.debug(
                "[%s] DEBUG разбор для RAG: topic_title=%r content_type=%s content_category=%s "
                "product=%s group_link=%r forum_topic_rows_chat=%s cache_hit_key=%s",
                self.name,
                topic_title,
                content_type,
                content_category,
                product_value,
                group_link,
                len(snap),
                snap.get(key) if message.message_thread_id else None,
            )

        meta: Dict[str, Any] = {
            "source": source_label,
            "content_type": content_type,
            "content_category": content_category,
            "product": product_value,
            "tags": tags,
            "added_by": uid,
            "date": date_iso,
            "topic_title": topic_title[:500],
        }
        visibility = await resolve_source_visibility(
            self._app,
            source_type=SOURCE_TELEGRAM_GROUP,
            source_key=str(message.chat.id),
            label=chat_label,
        )
        if yt_urls:
            meta.update(youtube_public_link(yt_urls[0]))
        elif group_link:
            apply_source_link_to_metadata(meta, group_link, visibility)
        if is_testimonial_chunk:
            meta.update(testimonial_metadata_overrides())
        else:
            role = infer_dialog_role(raw_text, content_category)
            if role:
                meta["role"] = role

        dedupe_salt = f"{message.chat.id}:{message.message_id}"

        def _index():
            return rs.materials.add_material_text(
                raw_text,
                base_metadata=meta,
                source=source_label,
                dedupe_salt=dedupe_salt,
            )

        n_chunks, ids = await asyncio.to_thread(_index)

        if n_chunks == 0 and raw_text.strip():
            dup_html = (
                "⊘ <b>Дубликат</b>\n"
                "Такой фрагмент уже есть в Chroma (совпал id чанка / дедуп)."
            )
            await self._rag_group_reply(
                message,
                dup_html,
                reply_to_message_id=message.message_id,
                parse_mode=ParseMode.HTML,
            )
            return

        if n_chunks == 0:
            await self._rag_group_reply(
                message,
                "⚠️ <b>Не записано</b> в Chroma (0 чанков) — см. логи.",
                reply_to_message_id=message.message_id,
                parse_mode=ParseMode.HTML,
            )
            return

        if self.config.rag_indexer_verbose:
            await _send_rag_debug_replies(
                message,
                raw_text=raw_text,
                meta=dict(meta),
                n_chunks=n_chunks,
                chunk_ids=list(ids),
                dedupe_salt=dedupe_salt,
            )
        else:
            logger.info(
                "[%s] индексация OK: %s чанков, msg_id=%s thread_id=%s",
                self.name,
                n_chunks,
                message.message_id,
                message.message_thread_id,
            )
            ok_html = _format_rag_index_success_reply(
                message,
                n_chunks=n_chunks,
                topic_title=topic_title,
                content_type=content_type,
                product_value=product_value,
                content_category=content_category,
                group_link=group_link,
            )
            await self._rag_group_reply(
                message,
                ok_html,
                reply_to_message_id=message.message_id,
                parse_mode=ParseMode.HTML,
            )


def _html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _split_for_pre(s: str, max_inner: int = _DEBUG_PRE_MAX) -> List[str]:
    if not s:
        return [""]
    return [s[i : i + max_inner] for i in range(0, len(s), max_inner)]


async def _send_rag_debug_replies(
    message: Message,
    *,
    raw_text: str,
    meta: Dict[str, Any],
    n_chunks: int,
    chunk_ids: List[str],
    dedupe_salt: str,
) -> None:
    """
    Отладка: все поля метаданных, id чанков в Chroma, соль дедупа,
    пояснение про chunk_index; затем полный текст от медиапроцессора (несколько сообщений при необходимости).
    """
    from config import config

    if not config.RAG_GROUP_INDEX_REPLIES:
        logger.info(
            "group_rag_indexer: verbose debug dump suppressed (RAG_GROUP_INDEX_REPLIES=0)"
        )
        return

    dump: Dict[str, Any] = {
        **meta,
        "_chromadb_expert_materials": {
            "dedupe_salt": dedupe_salt,
            "n_chunks_written": n_chunks,
            "chunk_ids": chunk_ids,
            "per_chunk": (
                "В Chroma на каждый чанк: metadatas = эти поля + chunk_index (0…n-1); "
                "documents[i] = текст i-го чанка после токенизации (не весь raw ниже)."
            ),
        },
    }
    meta_json = json.dumps(dump, ensure_ascii=False, indent=2)
    meta_esc = html_escape(meta_json)
    meta_parts = _split_for_pre(meta_esc)

    common = dict(
        reply_to_message_id=message.message_id,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    head = (
        "🔧 <b>RAG debug</b>\n"
        f"✅ В Chroma записано чанков: <code>{n_chunks}</code>\n\n"
        "<b>Метаданные (одинаковые на всех чанках + chunk_index в каждом):</b>\n"
    )
    await _rag_throttled_reply(message, head + f"<pre>{meta_parts[0]}</pre>", **common)
    for idx, frag in enumerate(meta_parts[1:], start=2):
        await _rag_throttled_reply(
            message,
            f"<b>Метаданные (фрагмент JSON {idx}/{len(meta_parts)}):</b>\n<pre>{frag}</pre>",
            **common,
        )

    raw = raw_text or ""
    raw_esc = html_escape(raw)
    raw_parts = _split_for_pre(raw_esc)
    n_sym = len(raw)
    t_head = f"<b>Текст от медиапроцессора (полный, {n_sym} символов):</b>\n<pre>{raw_parts[0]}</pre>"
    await _rag_throttled_reply(message, t_head, **common)
    for i, frag in enumerate(raw_parts[1:], start=2):
        await _rag_throttled_reply(
            message,
            f"<b>Текст (продолжение {i}/{len(raw_parts)}):</b>\n<pre>{frag}</pre>",
            **common,
        )
