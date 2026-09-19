"""
Политики доступа к боту после логирования входящего апдейта.

Текущий продукт («клуб»): бан = чёрный список; публичные команды
/start, /support, /payment, /club, /feedback, /subs — без проверки бана.

Чтобы переиспользовать шаблон в других проектах:
  * Наследуйте `AccessPolicy` и реализуйте `decide`.
  * Подставьте экземпляр в `AccessControlMiddleware(policy=...)` в `bot/core.py`.

Другие проекты: например `WhitelistUserIdsPolicy`, `SubscriptionRequiredPolicy` —
заменяют класс в одной точке сборки бота.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, FrozenSet, Iterable, Optional, Tuple

from aiogram.types import CallbackQuery, Message, Update

from bot.access.types import AccessContext, AccessDecision


class AccessPolicy(ABC):
    """Стратегия: публичность маршрута и итоговый ALLOW / DENY."""

    @abstractmethod
    async def decide(
        self, update: Update, ctx: AccessContext, event_obj
    ) -> AccessDecision:
        """Вызывается ПОСЛЕ записи в ``messages`` / ``interaction_logs``."""


def _first_command_token(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return text.split()[0].strip()


class BanBlacklistPolicy(AccessPolicy):
    """
    Публичные команды/callback'и без проверки бана; иначе блок при ``is_banned``.
    """

    def __init__(
        self,
        user_storage,
        public_commands: Iterable[str] | None = None,
        public_callback_prefixes: Iterable[str] | None = None,
    ):
        self.user_storage = user_storage
        self.public_commands: FrozenSet[str] = frozenset(
            public_commands
            or (
                "/start",
                "/support",
                "/payment",
                "/donat",
                "/club",
                "/feedback",
                "/subs",
            )
        )
        self.public_callback_prefixes: FrozenSet[str] = frozenset(
            public_callback_prefixes or ()
        )

    def is_public_route(self, event_type: str, event_obj) -> bool:
        if event_type == "message" and isinstance(event_obj, Message):
            cmd = _first_command_token(event_obj.text)
            return cmd in self.public_commands if cmd else False
        if event_type == "callback" and isinstance(event_obj, CallbackQuery):
            data = event_obj.data or ""
            return any(data.startswith(p) for p in self.public_callback_prefixes)
        return False

    async def decide(
        self, update: Update, ctx: AccessContext, event_obj
    ) -> AccessDecision:
        if self.is_public_route(ctx.event_type, event_obj):
            return AccessDecision.ALLOW_PUBLIC

        row = await self.user_storage.get_user(ctx.user_id)
        if row and row.get("is_banned", False):
            return AccessDecision.DENY_BANNED
        return AccessDecision.ALLOW


class WhitelistUserIdsPolicy(AccessPolicy):
    """
    Пример для «полезного» бота: доступ только у перечисленных user_id,
    плюс публичные маршруты (как у бана, но проверка белого списка).

    В новом проекте подставьте сюда свой список (из БД или env).
    """

    def __init__(
        self,
        user_storage,
        allowed_ids: FrozenSet[int],
        public_commands: Iterable[str] | None = None,
        public_callback_prefixes: Iterable[str] | None = None,
    ):
        # Можно использовать для загрузки allow-list из БД в других продуктах.
        self.user_storage = user_storage
        self.allowed_ids = allowed_ids
        self._inner = BanBlacklistPolicy(
            user_storage,
            public_commands=public_commands,
            public_callback_prefixes=public_callback_prefixes,
        )

    async def decide(
        self, update: Update, ctx: AccessContext, event_obj
    ) -> AccessDecision:
        if self._inner.is_public_route(ctx.event_type, event_obj):
            return AccessDecision.ALLOW_PUBLIC
        if ctx.user_id in self.allowed_ids:
            return AccessDecision.ALLOW
        return AccessDecision.DENY


class LicenseWhitelistPolicy(AccessPolicy):
    """Доступ к закрытым маршрутам только при действующей лицензии; бан запрещает всегда.

    Исключения: ``super_admin_id`` из конфига и записи в ``bot_admins`` — без лицензии.

    Публичные команды/callback остаются без проверки (онбординг, оплата, поддержка).
    """

    def __init__(
        self,
        user_storage,
        public_commands: Iterable[str] | None = None,
        public_callback_prefixes: Iterable[str] | None = None,
        *,
        super_admin_id: int = 0,
        admin_only_mode: bool = False,
    ):
        self.user_storage = user_storage
        self.super_admin_id = super_admin_id
        self.admin_only_mode = admin_only_mode
        if admin_only_mode:
            self.public_commands = frozenset(
                public_commands if public_commands is not None else ("/start",)
            )
            self.public_callback_prefixes = frozenset(
                public_callback_prefixes
                if public_callback_prefixes is not None
                else ("promote_admin:",)
            )
        else:
            self.public_commands: FrozenSet[str] = frozenset(
                public_commands
                or (
                    "/start",
                    "/support",
                    "/payment",
                    "/donat",
                    "/club",
                    "/feedback",
                    "/subs",
                    "/affiliate",
                )
            )
            self.public_callback_prefixes: FrozenSet[str] = frozenset(
                public_callback_prefixes or ("payment_",)
            )

    def is_public_route(self, event_type: str, event_obj) -> bool:
        if event_type == "message" and isinstance(event_obj, Message):
            cmd = _first_command_token(event_obj.text)
            return cmd in self.public_commands if cmd else False
        if event_type == "callback" and isinstance(event_obj, CallbackQuery):
            data = event_obj.data or ""
            return any(data.startswith(p) for p in self.public_callback_prefixes)
        return False

    async def decide(
        self, update: Update, ctx: AccessContext, event_obj
    ) -> AccessDecision:
        if self.is_public_route(ctx.event_type, event_obj):
            return AccessDecision.ALLOW_PUBLIC

        row = await self.user_storage.get_user(ctx.user_id)
        if row and row.get("is_banned", False):
            return AccessDecision.DENY_BANNED

        if self.super_admin_id and ctx.user_id == self.super_admin_id:
            return AccessDecision.ALLOW

        if await self.user_storage.is_bot_admin(ctx.user_id):
            return AccessDecision.ALLOW

        if self.admin_only_mode:
            return AccessDecision.DENY_ADMIN_ONLY

        if await self.user_storage.user_has_active_license(ctx.user_id):
            return AccessDecision.ALLOW

        return AccessDecision.DENY_NO_LICENSE


def parse_event(update: Update) -> Tuple[Optional[str], Any, Optional[int]]:
    """
    Внутренние типы событий пайплайна (message / callback / edited_message / reaction).
    Возвращает (event_type, event_obj, user_id).
    """
    if update.message:
        return "message", update.message, update.message.from_user.id
    if update.callback_query:
        return (
            "callback",
            update.callback_query,
            update.callback_query.from_user.id,
        )
    if update.edited_message:
        return (
            "edited_message",
            update.edited_message,
            update.edited_message.from_user.id,
        )
    if update.message_reaction:
        mr = update.message_reaction
        uid = mr.user.id if mr.user else None
        return "reaction", mr, uid
    return None, None, None
