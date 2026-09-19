"""
Mixin: сообщения, токены, общая статистика, interaction_logs,
история диалога (conversation_history).
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MessagesMixin:

    # =====================================================
    # messages: добавление, просмотр, edit/delete, dialog
    # =====================================================

    async def add_message(
        self,
        user_id: int,
        message_text: str,
        message_type: str,
        openai_thread_id: Optional[str] = None,
        openai_message_id: Optional[str] = None,
        assistant_id: Optional[str] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO messages
                    (user_id, content, role, thread_id, message_id, id_ass)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    user_id,
                    message_text,
                    message_type,
                    openai_thread_id,
                    openai_message_id,
                    assistant_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to add message: {e}")
            return False

    async def save_incoming_message(
        self,
        user_id: int,
        telegram_message_id: int,
        chat_id: int,
        content: str,
        message_type: str,
        subtype: Optional[str] = None,
        raw_data: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
        created_at: Optional[datetime] = None,
    ) -> Optional[int]:
        """Сохраняет входящее сообщение пользователя."""
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    """
                    INSERT INTO messages
                    (user_id, telegram_message_id, chat_id, content, sender_type,
                     message_type, subtype, raw_data, metadata, created_at)
                    VALUES ($1, $2, $3, $4, 'user', $5, $6, $7, $8, COALESCE($9, NOW()))
                    RETURNING id
                    """,
                    user_id,
                    telegram_message_id,
                    chat_id,
                    content,
                    message_type,
                    subtype,
                    json.dumps(raw_data) if raw_data else None,
                    json.dumps(metadata) if metadata else None,
                    created_at,
                )
        except Exception as e:
            logger.error(f"❌ Failed to save incoming message: {e}")
            return None

    async def save_outgoing_message(
        self,
        user_id: int,
        telegram_message_id: int,
        chat_id: int,
        content: str,
        reply_to_message_id: Optional[int] = None,
        raw_data: Optional[Dict] = None,
    ) -> Optional[int]:
        """Сохраняет исходящее сообщение бота."""
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    """
                    INSERT INTO messages
                    (user_id, telegram_message_id, chat_id, content, sender_type,
                     message_type, raw_data, created_at, reply_to_message_id)
                    VALUES ($1, $2, $3, $4, 'bot', 'text', $5, NOW(), $6)
                    RETURNING id
                    """,
                    user_id,
                    telegram_message_id,
                    chat_id,
                    content,
                    json.dumps(raw_data) if raw_data else None,
                    reply_to_message_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to save outgoing message: {e}")
            return None

    async def save_callback_message(
        self,
        user_id: int,
        telegram_message_id: int,
        chat_id: int,
        callback_data: str,
        subtype: str,
        raw_data: Optional[Dict] = None,
    ) -> Optional[int]:
        """Сохраняет callback (нажатие кнопки) как сообщение."""
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    """
                    INSERT INTO messages
                    (user_id, telegram_message_id, chat_id, content, sender_type,
                     message_type, subtype, raw_data, created_at)
                    VALUES ($1, $2, $3, $4, 'user', 'callback', $5, $6, NOW())
                    RETURNING id
                    """,
                    user_id,
                    telegram_message_id,
                    chat_id,
                    f"[нажата кнопка: {callback_data}]",
                    subtype,
                    json.dumps(raw_data) if raw_data else None,
                )
        except Exception as e:
            logger.error(f"❌ Failed to save callback message: {e}")
            return None

    async def mark_message_edited(
        self,
        user_id: int,
        telegram_message_id: int,
        chat_id: int,
        new_content: str,
        raw_data: Optional[Dict] = None,
    ) -> Optional[int]:
        """Создаёт новую версию сообщения (edited)."""
        try:
            async with self.get_connection() as conn:
                prev = await conn.fetchrow(
                    """
                    SELECT * FROM messages
                    WHERE user_id = $1 AND telegram_message_id = $2 AND chat_id = $3
                    ORDER BY version DESC NULLS LAST, created_at DESC
                    LIMIT 1
                    """,
                    user_id, telegram_message_id, chat_id,
                )
                if not prev:
                    logger.warning(
                        f"⚠️ Original message not found for editing: {telegram_message_id}"
                    )
                    return None

                message_id = await conn.fetchval(
                    """
                    INSERT INTO messages
                    (user_id, telegram_message_id, chat_id, content, sender_type,
                     message_type, subtype, raw_data, metadata, created_at,
                     edited_at, is_edited, version, reply_to_message_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW(), TRUE, $10, $11)
                    RETURNING id
                    """,
                    user_id,
                    telegram_message_id,
                    chat_id,
                    new_content,
                    prev["sender_type"],
                    prev["message_type"],
                    prev["subtype"],
                    json.dumps(raw_data) if raw_data else prev["raw_data"],
                    prev["metadata"],
                    (prev["version"] or 1) + 1,
                    prev["reply_to_message_id"],
                )
                logger.info(
                    f"✏️ Message {telegram_message_id} edited, new version: {message_id}"
                )
                return message_id
        except Exception as e:
            logger.error(f"❌ Failed to mark message as edited: {e}")
            return None

    async def mark_message_deleted(
        self,
        user_id: int,
        telegram_message_id: int,
        chat_id: int,
    ) -> bool:
        """Помечает сообщение как удалённое."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE messages
                    SET deleted_at = NOW()
                    WHERE user_id = $1 AND telegram_message_id = $2 AND chat_id = $3
                      AND deleted_at IS NULL
                    """,
                    user_id, telegram_message_id, chat_id,
                )
                logger.info(f"🗑️ Message {telegram_message_id} marked as deleted")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to mark message as deleted: {e}")
            return False

    async def get_assistant_messages_count(self, user_id: int) -> int:
        """Сколько сообщений роли 'assistant' в диалоге пользователя."""
        try:
            async with self.get_connection() as conn:
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM messages
                    WHERE user_id = $1 AND role = 'assistant'
                    """,
                    user_id,
                )
                return count or 0
        except Exception as e:
            logger.error(f"❌ Failed to get assistant messages count for user {user_id}: {e}")
            return 0

    # =====================================================
    # interaction_logs
    # =====================================================

    async def log_interaction(
        self,
        user_id: int,
        event_category: str,
        event_type: str,
        processing_time_ms: Optional[int] = None,
        message_id: Optional[int] = None,
        data: Optional[Dict] = None,
        *,
        update_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        chat_type: Optional[str] = None,
        telegram_message_id: Optional[int] = None,
        callback_data: Optional[str] = None,
        command: Optional[str] = None,
        source: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> bool:
        """Логирует событие взаимодействия в interaction_logs."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO interaction_logs
                    (user_id, event_category, event_type, processing_time_ms, message_id, data,
                     update_id, chat_id, chat_type, telegram_message_id,
                     callback_data, command, source, outcome)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    """,
                    user_id,
                    event_category,
                    event_type,
                    processing_time_ms,
                    message_id,
                    json.dumps(data) if data else "{}",
                    update_id,
                    chat_id,
                    chat_type,
                    telegram_message_id,
                    callback_data,
                    command,
                    source,
                    outcome,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to log interaction: {e}")
            return False

    # =====================================================
    # token_usage
    # =====================================================

    async def add_token_usage(
        self,
        user_id: int,
        message_id: Optional[str],
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO token_usage
                    (user_id, message_id, model, prompt_tokens, completion_tokens, total_tokens)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    user_id, message_id, model, prompt_tokens, completion_tokens, total_tokens,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to add token usage: {e}")
            return False

    async def add_token_usage_with_metadata(
        self,
        user_id: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        request_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        duration_sec: Optional[int] = None,
        metadata: Optional[Dict] = None,
        *,
        provider: str = "openai",
        request_kind: Optional[str] = None,
        raw_usage: Optional[str] = None,
        cached_input_tokens: Optional[int] = None,
        reasoning_output_tokens: Optional[int] = None,
    ) -> bool:
        """Расширенная запись usage (prompt/completion/total + провайдер + сырой JSON)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO token_usage
                    (user_id, model, prompt_tokens, completion_tokens, total_tokens,
                     request_id, thread_id, duration_sec, metadata,
                     provider, request_kind, raw_usage, cached_input_tokens,
                     reasoning_output_tokens)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                            $12::jsonb, $13, $14)
                    """,
                    user_id,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    request_id,
                    thread_id,
                    duration_sec,
                    json.dumps(metadata) if metadata else None,
                    provider,
                    request_kind,
                    raw_usage,
                    cached_input_tokens,
                    reasoning_output_tokens,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to add token usage with metadata: {e}")
            return False

    async def log_llm_completion_usage(
        self,
        user_id: int,
        provider: str,
        model: str,
        usage: Any,
        *,
        request_kind: str = "chat_completion",
        request_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        duration_sec: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Нормализует ``usage`` от LLM-провайдера и пишет строку в ``token_usage``."""
        from storage.db.llm_token_normalize import extract_token_counts_and_extras

        pt, ct, tt, raw, cached, reasoning = extract_token_counts_and_extras(usage)
        raw_json = json.dumps(raw) if raw else None
        if raw_json == "{}":
            raw_json = None
        return await self.add_token_usage_with_metadata(
            user_id,
            model,
            pt,
            ct,
            tt,
            request_id=request_id,
            thread_id=thread_id,
            duration_sec=duration_sec,
            metadata=metadata,
            provider=provider,
            request_kind=request_kind,
            raw_usage=raw_json,
            cached_input_tokens=cached,
            reasoning_output_tokens=reasoning,
        )

    # =====================================================
    # История переписки в личке (для AgentsClient/DeepSeek)
    # =====================================================
    #
    # Источник правды для контекста агента — таблица messages с фильтром
    # chat_type='private'. Туда автоматически попадают:
    #   * входящие пользователя (через InboundLoggingMiddleware);
    #   * исходящие бота (через OutgoingLoggingMiddleware);
    #   * рассылки, ответы саппорта, онбординг, лицензии — всё через тот же
    #     OutgoingLoggingMiddleware, потому что они отправляются bot.send_*.
    #
    # Старая таблица conversation_history оставлена для совместимости и архива
    # (см. миграцию 003), новый код в неё не пишет и из неё не читает.

    async def get_private_chat_history(
        self,
        user_id: int,
        limit: int = 20,
        include_callbacks: bool = False,
    ) -> List[Dict[str, str]]:
        """История DM пользователя в формате для LLM (role, content).

        Возвращает последние ``limit`` сообщений в хронологическом порядке.
        Callback-нажатия (`message_type='callback'`) по умолчанию исключены —
        они шумят в контексте; включить их можно ``include_callbacks=True``.
        """
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT role, content
                    FROM messages
                    WHERE user_id = $1
                      AND chat_type = 'private'
                      AND deleted_at IS NULL
                      AND content IS NOT NULL AND content <> ''
                      AND ($3 OR message_type <> 'callback')
                    ORDER BY created_at DESC, id DESC
                    LIMIT $2
                    """,
                    user_id, limit, include_callbacks,
                )
                return [
                    {"role": row["role"], "content": row["content"]}
                    for row in reversed(rows)
                ]
        except Exception as e:
            logger.error(
                f"❌ Failed to get private chat history for user {user_id}: {e}"
            )
            return []

    # ---- Legacy (deprecated) ------------------------------------------------
    # Оставлены, чтобы случайный вызов из старого кода не падал. Новый код
    # использует get_private_chat_history. Чтение из conversation_history
    # сохраняем как fallback на случай отката новой схемы.

    async def get_conversation_history(
        self, user_id: int, limit: int = 10
    ) -> List[Dict[str, str]]:
        """[DEPRECATED] Использовать get_private_chat_history."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT role, content FROM conversation_history
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id, limit,
                )
                return [
                    {"role": row["role"], "content": row["content"]}
                    for row in reversed(rows)
                ]
        except Exception as e:
            logger.error(f"❌ Failed to get conversation history for user {user_id}: {e}")
            return []

    async def save_conversation_message(
        self, user_id: int, role: str, content: str
    ) -> None:
        """[DEPRECATED] Новый код пишет в messages автоматически.

        Метод оставлен как no-op-обёртка для обратной совместимости.
        """
        logger.debug(
            "save_conversation_message is deprecated; messages are now logged "
            "automatically via middleware (user_id=%s role=%s)",
            user_id, role,
        )

    async def get_user_token_stats(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """Статистика токенов пользователя за N дней."""
        try:
            async with self.get_connection() as conn:
                total_stats = await conn.fetchrow(
                    """
                    SELECT
                        SUM(prompt_tokens) as total_prompt_tokens,
                        SUM(completion_tokens) as total_completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        COUNT(*) as request_count
                    FROM token_usage
                    WHERE user_id = $1
                      AND created_date >= CURRENT_DATE - make_interval(days => $2)
                    """,
                    user_id, days,
                )

                daily_stats = await conn.fetch(
                    """
                    SELECT
                        created_date,
                        SUM(prompt_tokens) as prompt_tokens,
                        SUM(completion_tokens) as completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        COUNT(*) as request_count
                    FROM token_usage
                    WHERE user_id = $1
                      AND created_date >= CURRENT_DATE - make_interval(days => $2)
                    GROUP BY created_date
                    ORDER BY created_date DESC
                    """,
                    user_id, days,
                )

                model_stats = await conn.fetch(
                    """
                    SELECT
                        model,
                        SUM(prompt_tokens) as prompt_tokens,
                        SUM(completion_tokens) as completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        COUNT(*) as request_count
                    FROM token_usage
                    WHERE user_id = $1
                      AND created_date >= CURRENT_DATE - make_interval(days => $2)
                    GROUP BY model
                    ORDER BY total_tokens DESC
                    """,
                    user_id, days,
                )

                provider_model_stats = await conn.fetch(
                    """
                    SELECT
                        provider,
                        request_kind,
                        model,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens,
                        SUM(total_tokens) AS total_tokens,
                        COUNT(*) AS request_count,
                        SUM(COALESCE(cached_input_tokens, 0)) AS cached_prompt_tokens_sum,
                        SUM(COALESCE(reasoning_output_tokens, 0)) AS reasoning_tokens_sum
                    FROM token_usage
                    WHERE user_id = $1
                      AND created_date >= CURRENT_DATE - make_interval(days => $2)
                    GROUP BY provider, request_kind, model
                    ORDER BY total_tokens DESC
                    """,
                    user_id,
                    days,
                )

                return {
                    "total": dict(total_stats) if total_stats else {},
                    "daily": [dict(row) for row in daily_stats],
                    "models": [dict(row) for row in model_stats],
                    "by_provider_request_model": [
                        dict(row) for row in provider_model_stats
                    ],
                }
        except Exception as e:
            logger.error(f"❌ Failed to get user token stats: {e}")
            return {}

    async def get_global_token_stats(self, days: int = 30) -> Dict[str, Any]:
        """Глобальная статистика токенов за N дней."""
        try:
            async with self.get_connection() as conn:
                total_stats = await conn.fetchrow(
                    """
                    SELECT
                        SUM(prompt_tokens) as total_prompt_tokens,
                        SUM(completion_tokens) as total_completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        COUNT(DISTINCT user_id) as unique_users,
                        COUNT(*) as total_requests
                    FROM token_usage
                    WHERE created_date >= CURRENT_DATE - make_interval(days => $1)
                    """,
                    days,
                )

                top_users = await conn.fetch(
                    """
                    SELECT
                        u.user_id,
                        u.username,
                        u.first_name,
                        SUM(t.total_tokens) as total_tokens,
                        COUNT(t.id) as request_count
                    FROM token_usage t
                    JOIN users u ON t.user_id = u.user_id
                    WHERE t.created_date >= CURRENT_DATE - make_interval(days => $1)
                    GROUP BY u.user_id, u.username, u.first_name
                    ORDER BY total_tokens DESC
                    LIMIT 10
                    """,
                    days,
                )

                daily_stats = await conn.fetch(
                    """
                    SELECT
                        created_date,
                        SUM(prompt_tokens) as prompt_tokens,
                        SUM(completion_tokens) as completion_tokens,
                        SUM(total_tokens) as total_tokens,
                        COUNT(DISTINCT user_id) as unique_users,
                        COUNT(*) as request_count
                    FROM token_usage
                    WHERE created_date >= CURRENT_DATE - make_interval(days => $1)
                    GROUP BY created_date
                    ORDER BY created_date DESC
                    """,
                    days,
                )

                by_provider = await conn.fetch(
                    """
                    SELECT
                        provider,
                        request_kind,
                        model,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens,
                        SUM(total_tokens) AS total_tokens,
                        COUNT(*) AS request_count,
                        SUM(COALESCE(cached_input_tokens, 0)) AS cached_prompt_tokens_sum,
                        SUM(COALESCE(reasoning_output_tokens, 0)) AS reasoning_tokens_sum
                    FROM token_usage
                    WHERE created_date >= CURRENT_DATE - make_interval(days => $1)
                    GROUP BY provider, request_kind, model
                    ORDER BY total_tokens DESC
                    """,
                    days,
                )

                return {
                    "total": dict(total_stats) if total_stats else {},
                    "top_users": [dict(row) for row in top_users],
                    "daily": [dict(row) for row in daily_stats],
                    "by_provider_request_model": [dict(row) for row in by_provider],
                }
        except Exception as e:
            logger.error(f"❌ Failed to get global token stats: {e}")
            return {}

    async def get_user_stats(self) -> Dict[str, Any]:
        """Общая статистика пользователей и сообщений."""
        try:
            async with self.get_connection() as conn:
                total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
                active_users = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM users
                    WHERE last_activity >= NOW() - INTERVAL '30 days'
                    """
                )
                total_messages = await conn.fetchval("SELECT COUNT(*) FROM messages")

                return {
                    "total_users": total_users,
                    "active_users_30d": active_users,
                    "total_messages": total_messages,
                }
        except Exception as e:
            logger.error(f"❌ Failed to get user stats: {e}")
            return {}

    # =====================================================
    # Расширенные запросы по messages / interaction_logs
    # =====================================================

    async def get_message_by_telegram_id(
        self,
        telegram_message_id: int,
        chat_id: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM messages
                    WHERE telegram_message_id = $1 AND chat_id = $2
                    ORDER BY version DESC NULLS LAST, created_at DESC
                    LIMIT 1
                    """,
                    telegram_message_id, chat_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get message by telegram_id: {e}")
            return None

    async def get_user_dialog(
        self,
        user_id: int,
        chat_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM messages
                    WHERE user_id = $1 AND chat_id = $2 AND deleted_at IS NULL
                    ORDER BY created_at ASC
                    LIMIT $3 OFFSET $4
                    """,
                    user_id, chat_id, limit, offset,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get user dialog: {e}")
            return []

    async def get_interaction_stats(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                categories = await conn.fetch(
                    """
                    SELECT event_category, COUNT(*) as count
                    FROM interaction_logs
                    WHERE user_id = $1
                      AND created_at >= NOW() - make_interval(days => $2)
                    GROUP BY event_category
                    """,
                    user_id, days,
                )

                avg_time = await conn.fetchval(
                    """
                    SELECT AVG(processing_time_ms)
                    FROM interaction_logs
                    WHERE user_id = $1
                      AND created_at >= NOW() - make_interval(days => $2)
                      AND processing_time_ms IS NOT NULL
                    """,
                    user_id, days,
                )

                daily = await conn.fetch(
                    """
                    SELECT DATE(created_at) as date, COUNT(*) as count
                    FROM messages
                    WHERE user_id = $1
                      AND created_at >= NOW() - make_interval(days => $2)
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                    """,
                    user_id, days,
                )

                return {
                    "categories": {row["event_category"]: row["count"] for row in categories},
                    "avg_processing_time_ms": avg_time,
                    "daily_messages": [dict(row) for row in daily],
                }
        except Exception as e:
            logger.error(f"❌ Failed to get interaction stats: {e}")
            return {}
