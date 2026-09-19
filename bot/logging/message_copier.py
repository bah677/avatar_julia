"""Копирование сообщений в БД для восстановления диалога и контекста агента."""

from __future__ import annotations

import asyncpg
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from aiogram.types import CallbackQuery, Chat, Message

from bot.media_processing.models import ProcessedMedia
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

# Что считаем "приватным" чатом для контекста агента.
PRIVATE_CHAT_TYPES = {"private"}


def _telegram_user_to_row(from_user: Any) -> Dict[str, Any]:
    """Поля для INSERT/UPSERT users (FK messages.user_id → users.user_id)."""
    return {
        "user_id": from_user.id,
        "username": getattr(from_user, "username", None),
        "first_name": getattr(from_user, "first_name", None),
        "last_name": getattr(from_user, "last_name", None),
        "language_code": getattr(from_user, "language_code", None),
        "is_premium": getattr(from_user, "is_premium", False),
    }


def _resolve_chat_type(chat: Optional[Chat], chat_id: Optional[int]) -> str:
    """Аккуратно определяет chat_type: 'private' | 'group' | 'supergroup' | 'channel'."""
    if chat is not None and getattr(chat, "type", None):
        return str(chat.type)
    if chat_id is None:
        return "unknown"
    return "private" if chat_id > 0 else "supergroup"


def _message_text_for_storage(message: Message) -> str:
    """Текст в ``messages.content``: Telegram отдаёт plain text + entities — для истории LLM нужен HTML."""
    if message.text is None and message.caption is None:
        return ""
    try:
        return message.html_text
    except Exception:
        return message.text or message.caption or ""


class MessageCopier:
    """Сохраняет полные копии всех сообщений (входящих, исходящих, callback'ов)."""

    def __init__(self, user_storage: UserStorage):
        self.user_storage = user_storage

    # ------------------------------------------------------------------ #
    # ВХОДЯЩИЕ                                                            #
    # ------------------------------------------------------------------ #
    async def save_incoming(
        self,
        message: Message,
        processed: Optional[ProcessedMedia] = None,
    ) -> Optional[int]:
        """Сохраняет входящее сообщение пользователя."""
        try:
            if message.from_user is None:
                logger.warning("save_incoming: no from_user, skip persistence")
                return None
            # InboundLoggingMiddleware выполняется до хендлеров — строки в users ещё нет → FK violation.
            await self.user_storage.add_or_update_user(_telegram_user_to_row(message.from_user))

            user_id = message.from_user.id
            chat_type = _resolve_chat_type(message.chat, message.chat.id)

            message_type = self._get_message_type(message)
            subtype = self._get_subtype(message)

            if processed and processed.text:
                content = processed.text
            else:
                content = _message_text_for_storage(message)

            metadata = self._collect_media_metadata(message, processed)

            async with self.user_storage.db.get_connection() as conn:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO messages
                      (user_id, telegram_message_id, chat_id, chat_type, content,
                       sender_type, role, message_type, subtype,
                       raw_data, metadata, created_at)
                    VALUES ($1, $2, $3, $4, $5,
                            'user', 'user', $6, $7,
                            $8, $9, $10::timestamptz)
                    RETURNING id
                    """,
                    user_id,
                    message.message_id,
                    message.chat.id,
                    chat_type,
                    content,
                    message_type,
                    subtype,
                    self._safe_json(message),
                    json.dumps(metadata),
                    message.date,
                )
                logger.debug("✅ incoming saved id=%s chat_type=%s", row_id, chat_type)
                return row_id

        except Exception as e:
            logger.error("❌ save_incoming failed: %s", e, exc_info=True)
            return None

    async def save_synthetic_private_user_text(
        self,
        *,
        user_id: int,
        chat_id: int,
        content: str,
        callback_query_id: str,
        callback_data: str,
    ) -> Optional[int]:
        """Вставка «как пользователь написал text» после нажатия inline-кнопки (личка).

        Ряд с callback в ``messages`` остаётся от middleware; здесь строка видна агенту
        (``message_type='text'``, ``message_type <> 'callback'`` в выборке истории).

        ``telegram_message_id`` отрицательный синтетический, чтобы не дублировать
        telegram id реальных сообщений и обойти уникальный индекс входящих.
        """
        if not content:
            return None
        try:
            await self.user_storage.add_or_update_user({"user_id": user_id})
            synthetic_mid = -(abs(hash(f"{callback_query_id}:{callback_data}:{content}")) % (2**31 - 1) or 1)
            meta: Dict[str, Any] = {
                "origin": "inline_keyboard",
                "callback_data": callback_data,
                "callback_query_id": str(callback_query_id),
            }
            raw_compact = {"kind": "synthetic_user_text_from_button", **meta}
            now = datetime.now(timezone.utc)
            async with self.user_storage.db.get_connection() as conn:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO messages
                      (user_id, telegram_message_id, chat_id, chat_type, content,
                       sender_type, role, message_type, subtype,
                       raw_data, metadata, created_at)
                    VALUES ($1, $2, $3, 'private', $4,
                            'user', 'user', 'text', $5,
                            $6, $7, $8::timestamptz)
                    RETURNING id
                    """,
                    user_id,
                    synthetic_mid,
                    chat_id,
                    content,
                    "onboarding_pick",
                    json.dumps(raw_compact),
                    json.dumps(meta),
                    now,
                )
                logger.debug("✅ synthetic user text saved id=%s", row_id)
                return row_id
        except Exception as e:
            logger.error("❌ save_synthetic_private_user_text failed: %s", e, exc_info=True)
            return None

    # ------------------------------------------------------------------ #
    # ИСХОДЯЩИЕ                                                           #
    # ------------------------------------------------------------------ #
    async def save_outgoing(
        self,
        message: Optional[Message] = None,
        *,
        user_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        text: Optional[str] = None,
        chat_type: Optional[str] = None,
        message_type: str = "text",
        subtype: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        reply_to_message_id: Optional[int] = None,
        thread_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Optional[int]:
        """Сохраняет исходящее сообщение бота.

        Можно вызывать двумя способами:
          * передать готовый Message (то, что вернул bot.send_*) — все поля
            достанутся из объекта;
          * либо явно передать user_id/chat_id/text/chat_type — для случаев,
            когда отправка не вернула Message (или мы логируем "виртуальное"
            исходящее, например результат рассылки).

        ``source`` — короткий маркер откуда пришло (assistant, mailing,
        admin_reply, support_reply, onboarding, license, system…), кладётся
        в metadata.

        Исходящие в группы/супергруппы/каналы без явного ``user_id`` не сохраняются:
        в ``messages.user_id`` хранится получатель диалога (личка), NOT NULL.
        """
        try:
            meta = dict(metadata or {})
            if source:
                meta.setdefault("source", source)

            if message is not None:
                eff_chat_id = message.chat.id
                eff_chat_type = chat_type or _resolve_chat_type(message.chat, eff_chat_id)
                # user_id получателя имеет смысл только в личке (там chat.id == user.id).
                # Для группового сообщения бота получателя как такового нет — пишем NULL.
                if user_id is not None:
                    target_user_id = user_id
                elif eff_chat_type == "private":
                    target_user_id = eff_chat_id
                else:
                    target_user_id = None
                if text is not None:
                    eff_text = text
                else:
                    eff_text = _message_text_for_storage(message)
                tg_message_id = message.message_id
                created_at = message.date or datetime.now(timezone.utc)
                raw_data = self._safe_json(message)
                resolved_reply_to = reply_to_message_id
                if (
                    message.reply_to_message is not None
                    and getattr(message.reply_to_message, "message_id", None) is not None
                    and resolved_reply_to is None
                ):
                    resolved_reply_to = message.reply_to_message.message_id
            else:
                if user_id is None:
                    logger.warning("save_outgoing: user_id is required when message=None")
                    return None
                target_user_id = user_id
                eff_chat_id = chat_id if chat_id is not None else user_id
                eff_chat_type = chat_type or _resolve_chat_type(None, eff_chat_id)
                eff_text = text or ""
                tg_message_id = None
                created_at = datetime.now(timezone.utc)
                raw_data = None
                resolved_reply_to = reply_to_message_id

            if target_user_id is None:
                chat_ref = eff_chat_id if message is not None else chat_id
                logger.debug(
                    "save_outgoing: пропуск (нет user_id для messages): chat_id=%s type=%s",
                    chat_ref,
                    eff_chat_type,
                )
                return None

            # В личке user_id в messages ссылается на users; исходящее может уйти до полного онбординга.
            await self.user_storage.add_or_update_user({"user_id": target_user_id})

            async with self.user_storage.db.get_connection() as conn:
                meta_json = json.dumps(meta)

                row_id = None
                # Telegram сохраняет message_id при edit_message_* / замене клавиатуры.
                # Уникальный индекс (chat_id, telegram_message_id) не даёт второй INSERT.
                # Если запись уже есть — обновляем контент/сырьё метадату.
                if tg_message_id is not None:
                    row_id = await conn.fetchval(
                        """
                        UPDATE messages
                           SET content = $1,
                               raw_data = $2::jsonb,
                               message_type = $3,
                               subtype = COALESCE($4::varchar, subtype),
                               chat_type = COALESCE($5::varchar, chat_type),
                               metadata =
                                 COALESCE(metadata, '{}'::jsonb) || $6::jsonb,
                               edited_at = NOW(),
                               is_edited = TRUE,
                               reply_to_message_id =
                                 COALESCE($7::bigint, reply_to_message_id),
                               thread_id = COALESCE($8::varchar, thread_id),
                               user_id = COALESCE($9::bigint, user_id)
                         WHERE chat_id = $10
                           AND telegram_message_id = $11
                           AND sender_type = 'bot'
                           AND role = 'assistant'
                           AND message_type <> 'callback'
                        RETURNING id
                        """,
                        eff_text,
                        raw_data,
                        message_type,
                        subtype,
                        eff_chat_type,
                        meta_json,
                        resolved_reply_to,
                        thread_id,
                        target_user_id,
                        eff_chat_id,
                        tg_message_id,
                    )

                if row_id is None:
                    try:
                        row_id = await conn.fetchval(
                            """
                            INSERT INTO messages
                              (user_id, telegram_message_id, chat_id, chat_type, content,
                               sender_type, role, message_type, subtype,
                               raw_data, metadata, created_at,
                               reply_to_message_id, thread_id)
                            VALUES ($1, $2, $3, $4, $5,
                                    'bot', 'assistant', $6, $7,
                                    $8, $9, $10::timestamptz,
                                    $11, $12)
                            RETURNING id
                            """,
                            target_user_id,
                            tg_message_id,
                            eff_chat_id,
                            eff_chat_type,
                            eff_text,
                            message_type,
                            subtype,
                            raw_data,
                            meta_json,
                            created_at,
                            resolved_reply_to,
                            thread_id,
                        )
                    except asyncpg.UniqueViolationError:
                        row_id = await conn.fetchval(
                            """
                            UPDATE messages
                               SET content = $1,
                                   raw_data = $2::jsonb,
                                   message_type = $3,
                                   subtype = COALESCE($4::varchar, subtype),
                                   chat_type = COALESCE($5::varchar, chat_type),
                                   metadata =
                                     COALESCE(metadata, '{}'::jsonb) || $6::jsonb,
                                   edited_at = NOW(),
                                   is_edited = TRUE,
                                   reply_to_message_id =
                                     COALESCE($7::bigint, reply_to_message_id),
                                   thread_id = COALESCE($8::varchar, thread_id),
                                   user_id = COALESCE($9::bigint, user_id)
                             WHERE chat_id = $10
                               AND telegram_message_id = $11
                               AND telegram_message_id IS NOT NULL
                               AND sender_type = 'bot'
                               AND role = 'assistant'
                               AND message_type <> 'callback'
                            RETURNING id
                            """,
                            eff_text,
                            raw_data,
                            message_type,
                            subtype,
                            eff_chat_type,
                            meta_json,
                            resolved_reply_to,
                            thread_id,
                            target_user_id,
                            eff_chat_id,
                            tg_message_id,
                        )
                        if row_id is None:
                            raise

                logger.debug(
                    "✅ outgoing saved id=%s chat_type=%s source=%s",
                    row_id, eff_chat_type, meta.get("source"),
                )
                return row_id

        except Exception as e:
            logger.error("❌ save_outgoing failed: %s", e, exc_info=True)
            return None

    # ------------------------------------------------------------------ #
    # CALLBACK                                                            #
    # ------------------------------------------------------------------ #
    async def save_callback(
        self,
        callback_query: CallbackQuery,
        user_id: int,
        subtype: Optional[str] = None,
    ) -> Optional[int]:
        """Сохраняет нажатие кнопки как сообщение."""
        try:
            if callback_query.from_user is not None:
                await self.user_storage.add_or_update_user(
                    _telegram_user_to_row(callback_query.from_user)
                )
            else:
                await self.user_storage.add_or_update_user({"user_id": user_id})

            cb_message = callback_query.message
            chat = cb_message.chat if cb_message else None
            chat_id = chat.id if chat else None
            chat_type = _resolve_chat_type(chat, chat_id)

            async with self.user_storage.db.get_connection() as conn:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO messages
                      (user_id, telegram_message_id, chat_id, chat_type, content,
                       sender_type, role, message_type, subtype,
                       raw_data, created_at)
                    VALUES ($1, $2, $3, $4, $5,
                            'user', 'user', 'callback', $6,
                            $7, $8::timestamptz)
                    RETURNING id
                    """,
                    user_id,
                    cb_message.message_id if cb_message else None,
                    chat_id,
                    chat_type,
                    f"[нажата кнопка: {callback_query.data}]",
                    subtype or callback_query.data,
                    self._safe_json(callback_query),
                    datetime.now(timezone.utc),
                )
                logger.debug("✅ callback saved id=%s data=%s", row_id, callback_query.data)
                return row_id

        except Exception as e:
            logger.error("❌ save_callback failed: %s", e, exc_info=True)
            return None

    # ------------------------------------------------------------------ #
    # ОТРЕДАКТИРОВАННОЕ                                                   #
    # ------------------------------------------------------------------ #
    async def save_edited_message(
        self,
        message: Message,
        user_id: int,
        previous_version_id: Optional[int] = None,
    ) -> Optional[int]:
        """Сохраняет правку сообщения как новую версию."""
        try:
            if message.from_user is not None:
                await self.user_storage.add_or_update_user(_telegram_user_to_row(message.from_user))
            else:
                await self.user_storage.add_or_update_user({"user_id": user_id})

            chat_type = _resolve_chat_type(message.chat, message.chat.id)
            async with self.user_storage.db.get_connection() as conn:
                prev = None
                if previous_version_id is not None:
                    prev = await conn.fetchrow(
                        "SELECT * FROM messages WHERE id = $1", previous_version_id
                    )
                if prev is None:
                    # ищем по telegram-id
                    prev = await conn.fetchrow(
                        """
                        SELECT * FROM messages
                        WHERE chat_id = $1 AND telegram_message_id = $2
                        ORDER BY id DESC LIMIT 1
                        """,
                        message.chat.id, message.message_id,
                    )
                if prev is None:
                    logger.warning(
                        "edit ignored: no previous record (chat=%s tg_id=%s)",
                        message.chat.id, message.message_id,
                    )
                    return None

                new_version = (prev["version"] or 1) + 1
                row_id = await conn.fetchval(
                    """
                    INSERT INTO messages
                      (user_id, telegram_message_id, chat_id, chat_type, content,
                       sender_type, role, message_type, subtype,
                       raw_data, metadata, created_at,
                       edited_at, is_edited, version)
                    VALUES ($1, $2, $3, $4, $5,
                            $6, $7, $8, $9,
                            $10, $11, $12::timestamptz,
                            $13::timestamptz, TRUE, $14)
                    RETURNING id
                    """,
                    user_id,
                    prev["telegram_message_id"],
                    prev["chat_id"],
                    chat_type,
                    message.text or message.caption or "",
                    prev["sender_type"],
                    prev["role"],
                    prev["message_type"],
                    prev["subtype"],
                    self._safe_json(message),
                    prev["metadata"],
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                    new_version,
                )
                logger.info("✏️ edited saved id=%s version=%s", row_id, new_version)
                return row_id

        except Exception as e:
            logger.error("❌ save_edited_message failed: %s", e, exc_info=True)
            return None

    # ------------------------------------------------------------------ #
    # ОБНОВЛЕНИЕ КОНТЕНТА (после распознавания медиа)                     #
    # ------------------------------------------------------------------ #
    async def update_message_content(
        self,
        message_id: int,
        content: str,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Обновляет content/metadata для уже сохранённого сообщения."""
        try:
            async with self.user_storage.db.get_connection() as conn:
                current = await conn.fetchrow(
                    "SELECT metadata FROM messages WHERE id = $1", message_id
                )
                if not current:
                    logger.warning("update_message_content: id=%s not found", message_id)
                    return False

                cur_meta = current["metadata"]
                if isinstance(cur_meta, str):
                    cur_meta = json.loads(cur_meta) if cur_meta else {}
                elif cur_meta is None:
                    cur_meta = {}
                if metadata:
                    cur_meta.update(metadata)

                await conn.execute(
                    """
                    UPDATE messages
                       SET content = $1,
                           metadata = $2,
                           processing_time_ms = $3
                     WHERE id = $4
                    """,
                    content,
                    json.dumps(cur_meta),
                    (metadata or {}).get("processing_time_ms"),
                    message_id,
                )
                return True
        except Exception as e:
            logger.error("❌ update_message_content failed: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------ #
    # ВНУТРЕННИЕ ХЕЛПЕРЫ                                                  #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_json(obj: Any) -> Optional[str]:
        try:
            if hasattr(obj, "model_dump"):
                return json.dumps(obj.model_dump(exclude_none=True), default=str)
            if hasattr(obj, "dict"):
                return json.dumps(obj.dict(), default=str)
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _get_message_type(message: Message) -> str:
        if message.text:
            return "command" if message.text.startswith("/") else "text"
        if message.voice:
            return "voice"
        if message.audio:
            return "audio"
        if message.video:
            return "video"
        if message.video_note:
            return "video_note"
        if message.photo:
            return "photo"
        if message.document:
            return "document"
        if message.sticker:
            return "sticker"
        if message.location:
            return "location"
        if message.contact:
            return "contact"
        return "unknown"

    @staticmethod
    def _get_subtype(message: Message) -> Optional[str]:
        if message.text and message.text.startswith("/"):
            return message.text[1:].split()[0]
        return None

    @staticmethod
    def _collect_media_metadata(
        message: Message, processed: Optional[ProcessedMedia]
    ) -> Dict[str, Any]:
        meta: Dict[str, Any] = {}
        if processed and processed.metadata:
            meta.update(processed.metadata)

        if message.voice:
            meta.update(duration=message.voice.duration, file_size=message.voice.file_size)
        elif message.photo:
            meta.update(width=message.photo[-1].width, height=message.photo[-1].height)
        elif message.video:
            meta.update(
                duration=message.video.duration,
                width=message.video.width,
                height=message.video.height,
                file_size=message.video.file_size,
            )
        elif message.video_note:
            meta.update(
                duration=message.video_note.duration,
                length=message.video_note.length,
                file_size=message.video_note.file_size,
            )
        elif message.audio:
            meta.update(
                duration=message.audio.duration,
                title=message.audio.title,
                performer=message.audio.performer,
                file_size=message.audio.file_size,
            )
        elif message.document:
            meta.update(
                file_name=message.document.file_name,
                mime_type=message.document.mime_type,
                file_size=message.document.file_size,
            )
        elif message.sticker:
            meta.update(
                emoji=message.sticker.emoji,
                set_name=message.sticker.set_name,
                is_animated=message.sticker.is_animated,
                is_video=message.sticker.is_video,
            )
        elif message.location:
            meta.update(
                latitude=message.location.latitude,
                longitude=message.location.longitude,
            )
        elif message.contact:
            meta.update(
                first_name=message.contact.first_name,
                last_name=message.contact.last_name,
                phone_number=message.contact.phone_number,
                has_phone=bool(message.contact.phone_number),
            )
        return meta
