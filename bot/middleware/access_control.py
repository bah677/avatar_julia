"""Проверка доступа по инжектируемой политике после логирования входа."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message, Update

from bot.access.policies import AccessPolicy
from bot.access.types import AccessContext, AccessDecision

logger = logging.getLogger(__name__)


DENY_MESSAGE_HTML_DEFAULT = (
    "<b>🚫 Доступ запрещен</b>\n\n"
    "Ваш аккаунт был заблокирован.\n"
    "Если вы считаете, что это ошибка, обратитесь в поддержку."
)

DENY_NO_LICENSE_MESSAGE_HTML_DEFAULT = (
    "<b>Нужен доступ</b>\n\n"
    "Функции бота доступны при активной подписке.\n"
    "Оформить: /payment\n"
    "Поддержка: /support"
)

DENY_ADMIN_ONLY_MESSAGE_HTML_DEFAULT = (
    "<b>Доступ по приглашению</b>\n\n"
    "Бот работает только для администраторов из белого списка.\n"
    "Заявка на доступ отправлена супер-администратору — ожидайте решения.\n\n"
    "Пока доступ не выдан, остальные команды недоступны."
)


class AccessControlMiddleware(BaseMiddleware):
    def __init__(
        self,
        policy: AccessPolicy,
        deny_banned_message_html: str = DENY_MESSAGE_HTML_DEFAULT,
        deny_no_license_message_html: str = DENY_NO_LICENSE_MESSAGE_HTML_DEFAULT,
        deny_admin_only_message_html: str = DENY_ADMIN_ONLY_MESSAGE_HTML_DEFAULT,
        *,
        deny_message_html: Optional[str] = None,
    ):
        super().__init__()
        self.policy = policy
        # Совместимость: раньше передавали один аргумент deny_message_html.
        if deny_message_html is not None:
            self.deny_banned_message_html = deny_message_html
        else:
            self.deny_banned_message_html = deny_banned_message_html
        self.deny_no_license_message_html = deny_no_license_message_html
        self.deny_admin_only_message_html = deny_admin_only_message_html

    async def _reply_denied(self, event_obj, html: str) -> None:
        try:
            if isinstance(event_obj, CallbackQuery):
                if event_obj.message:
                    await event_obj.message.edit_text(
                        html,
                        parse_mode=ParseMode.HTML,
                    )
                await event_obj.answer()
            elif isinstance(event_obj, Message):
                await event_obj.answer(html, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error("Error sending access denied reply: %s", e)

    async def __call__(self, handler, event: Update, data: Dict[str, Any]):
        event_type = data.get("access_event_type")
        event_obj = data.get("access_event_obj")
        user_id = data.get("access_user_id")

        if event_type is None or event_obj is None or user_id is None:
            return await handler(event, data)

        raw = data.get("access_raw_event_type", event_type)
        ctx = AccessContext(user_id=user_id, event_type=event_type, raw_event_type=str(raw))

        decision = await self.policy.decide(event, ctx, event_obj)

        if decision in (
            AccessDecision.DENY,
            AccessDecision.DENY_BANNED,
        ):
            await self._reply_denied(event_obj, self.deny_banned_message_html)
            return
        if decision == AccessDecision.DENY_NO_LICENSE:
            await self._reply_denied(event_obj, self.deny_no_license_message_html)
            return
        if decision == AccessDecision.DENY_ADMIN_ONLY:
            await self._reply_denied(event_obj, self.deny_admin_only_message_html)
            return

        return await handler(event, data)
