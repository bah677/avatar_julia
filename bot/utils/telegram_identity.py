"""Имя бота в Telegram (username) для ссылок без хардкода."""

import logging
from typing import Optional

from aiogram import Bot

from config import config

logger = logging.getLogger(__name__)


def _normalize_bot_username(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    u = raw.strip().lstrip("@")
    return u or None


async def resolve_telegram_bot_username(bot: Bot) -> Optional[str]:
    """
    Username бота для ссылок ``https://t.me/<username>``.

    Сначала ``get_me()``; при ошибке или пустом поле — опционально
    ``config.TELEGRAM_BOT_USERNAME`` (без ``@``), из окружения.
    """
    try:
        me = await bot.get_me()
        name = (getattr(me, "username", None) or "").strip()
        if name:
            return name
    except Exception as e:
        logger.error("❌ resolve_telegram_bot_username: get_me failed: %s", e)

    fallback = _normalize_bot_username(getattr(config, "TELEGRAM_BOT_USERNAME", None))
    if fallback:
        logger.warning("Using TELEGRAM_BOT_USERNAME from config as bot username fallback")
    return fallback


async def resolve_telegram_bot_display_name(bot: Bot) -> str:
    """
    Отображаемое имя бота (поле <b>Name</b> в BotFather), не @username.

    ``User.first_name`` в ответе ``getMe()`` для бота — это как раз bot name.
    """
    try:
        me = await bot.get_me()
        if me:
            first = (getattr(me, "first_name", None) or "").strip()
            if first:
                return first
            last = (getattr(me, "last_name", None) or "").strip()
            if last:
                return last
    except Exception as e:
        logger.error("resolve_telegram_bot_display_name: get_me failed: %s", e)
    return "аватар"
