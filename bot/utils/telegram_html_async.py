"""Асинхронная нормализация ответа: DeepSeek приводит ответ к HTML (с контекстом в format_reply)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from bot.utils.telegram_html import (
    looks_like_telegram_html,
    normalize_fixed_markdown_phrases,
    normalize_llm_reply_for_telegram,
    sanitize_telegram_html,
)

if TYPE_CHECKING:
    from openai_client.agents_client import AgentsClient

logger = logging.getLogger(__name__)


async def normalize_llm_reply_for_telegram_async(
    text: Optional[str],
    *,
    user_id: int,
    agents_client: Optional["AgentsClient"] = None,
) -> str:
    """
    Если ответ уже с допустимыми HTML-тегами Telegram — только sanitize.

    Иначе вызываем ``AgentsClient.format_reply_to_telegram_html`` (DeepSeek): туда же
    попадает хвост истории из БД — модель видит контекст диалога при разметке.

    Без ``agents_client`` или при ошибке — синхронная эвристика + escape.
    """
    if not text or not str(text).strip():
        return normalize_llm_reply_for_telegram(text)

    raw = normalize_fixed_markdown_phrases(text.strip())

    if looks_like_telegram_html(raw):
        return sanitize_telegram_html(raw)

    if agents_client is None:
        logger.warning("HTML format skipped: agents_client missing (user_id=%s)", user_id)
        return normalize_llm_reply_for_telegram(text)

    try:
        formatted = await agents_client.format_reply_to_telegram_html(raw, user_id)
        if formatted and formatted.strip():
            return sanitize_telegram_html(formatted.strip())
        logger.warning("DeepSeek HTML format returned empty (user_id=%s), fallback", user_id)
    except Exception as e:
        logger.error("format_reply_to_telegram_html failed: %s", e, exc_info=True)

    return normalize_llm_reply_for_telegram(text)
