# bot/features/license_service.py
import logging
from typing import Any, Dict, Optional

from config import config

logger = logging.getLogger(__name__)


class AccessResult:
    """Результат проверки доступа"""

    def __init__(
        self,
        has_access: bool,
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.has_access = has_access
        self.reason = reason
        self.details = details or {}

    @property
    def is_denied(self) -> bool:
        return not self.has_access


class LicenseService:
    """Проверка доступа: бан и действующая лицензия (белый список)."""

    def __init__(self, user_storage):
        self.user_storage = user_storage

    async def check_access(self, user_id: int) -> bool:
        """Доступ: суперадмин / bot_admins / без бана и с активной лицензией."""
        try:
            user_data = await self.user_storage.get_user(user_id)
            if user_data and user_data.get("is_banned", False):
                logger.info("🚫 User %s banned, access denied", user_id)
                return False
            if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
                return True
            if await self.user_storage.is_bot_admin(user_id):
                return True
            licensed = await self.user_storage.user_has_active_license(user_id)
            if not licensed:
                logger.info("🔒 User %s has no active license", user_id)
                return False
            return True
        except Exception as e:
            logger.error("❌ Error checking access for user_id=%s: %s", user_id, e)
            return False

    async def can_ask_question(self, user_id: int) -> AccessResult:
        try:
            user_data = await self.user_storage.get_user(user_id)
            if user_data and user_data.get("is_banned", False):
                return AccessResult(
                    has_access=False,
                    reason="user_banned",
                    details={"type": "denied", "reason": "banned", "user_id": user_id},
                )
            if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
                return AccessResult(
                    has_access=True,
                    reason="super_admin",
                    details={"type": "granted", "user_id": user_id},
                )
            if await self.user_storage.is_bot_admin(user_id):
                return AccessResult(
                    has_access=True,
                    reason="bot_admin",
                    details={"type": "granted", "user_id": user_id},
                )
            if not await self.user_storage.user_has_active_license(user_id):
                return AccessResult(
                    has_access=False,
                    reason="no_active_license",
                    details={"type": "denied", "reason": "no_license", "user_id": user_id},
                )
            return AccessResult(
                has_access=True,
                reason="access_granted",
                details={"type": "granted", "user_id": user_id},
            )
        except Exception as e:
            logger.error("❌ Error checking question access for user_id=%s: %s", user_id, e)
            return AccessResult(
                has_access=False,
                reason="error",
                details={"error": str(e), "user_id": user_id},
            )

    async def get_access_info(self, user_id: int) -> Dict[str, Any]:
        try:
            user_data = await self.user_storage.get_user(user_id)
            is_banned = user_data.get("is_banned", False) if user_data else False
            has_active = await self.user_storage.user_has_active_license(user_id)
            is_super = bool(config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID)
            in_bot_admins = await self.user_storage.is_bot_admin(user_id)
            has_license = has_active or is_super or in_bot_admins

            return {
                "has_license": has_license,
                "has_active_subscription": has_active,
                "is_super_admin": is_super,
                "is_bot_admin": in_bot_admins,
                "questions": {
                    "asked": 0,
                    "remaining": 999999,
                    "limit": 999999,
                },
                "is_banned": is_banned,
            }
        except Exception as e:
            logger.error("❌ Error getting access info for user_id=%s: %s", user_id, e)
            return {"error": str(e)}
