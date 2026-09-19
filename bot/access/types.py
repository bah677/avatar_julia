"""Общие типы для политик доступа к боту."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AccessDecision(str, Enum):
    """Результат проверки доступа после журналирования апдейта."""

    # Публичные маршруты (команда / callback-префиксы) — доступ без проверки бана/подписки.
    ALLOW_PUBLIC = "allow_public"
    # Обычный доступ разрешён (прошли бан/whitelist/и т. д.).
    ALLOW = "allow"
    # Доступ закрыт (legacy: как отказ по бану; см. middleware).
    DENY = "deny"
    # Закрыто: пользователь в бане (строже лицензии).
    DENY_BANNED = "deny_banned"
    # Закрыто: нет действующей лицензии (белый список).
    DENY_NO_LICENSE = "deny_no_license"
    # Режим только админы: не супер и не в bot_admins (лицензия не помогает).
    DENY_ADMIN_ONLY = "deny_admin_only"


@dataclass(frozen=True)
class AccessContext:
    """Минимальный контекст одного входящего апдейта для политики."""

    user_id: int
    event_type: str
    # message | callback_query | edited_message | message_reaction
    raw_event_type: str
