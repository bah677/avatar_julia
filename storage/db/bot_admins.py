"""
Mixin: администраторы бота (полный доступ без лицензии; назначает только суперадмин из .env).
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class BotAdminsMixin:
    async def is_bot_admin(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchval(
                    "SELECT 1 FROM bot_admins WHERE user_id = $1",
                    user_id,
                )
                return row is not None
        except Exception as e:
            logger.error("❌ is_bot_admin user=%s: %s", user_id, e)
            return False

    async def add_bot_admin(
        self,
        target_user_id: int,
        *,
        added_by: Optional[int] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO bot_admins (user_id, added_by)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) DO UPDATE SET
                        added_by = COALESCE(EXCLUDED.added_by, bot_admins.added_by)
                    """,
                    target_user_id,
                    added_by,
                )
            logger.info(
                "✅ bot_admins: добавлен/обновлён user_id=%s (кто добавил: %s)",
                target_user_id,
                added_by,
            )
            return True
        except Exception as e:
            logger.error("❌ add_bot_admin user=%s: %s", target_user_id, e)
            return False

    async def remove_bot_admin(self, target_user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                removed = await conn.fetchval(
                    "DELETE FROM bot_admins WHERE user_id = $1 RETURNING user_id",
                    target_user_id,
                )
            if removed is not None:
                logger.info("✅ bot_admins: удалён user_id=%s", target_user_id)
            else:
                logger.info("ℹ️ bot_admins: user_id=%s не был в списке", target_user_id)
            return True
        except Exception as e:
            logger.error("❌ remove_bot_admin user=%s: %s", target_user_id, e)
            return False

    async def list_bot_admin_ids(self) -> List[int]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    "SELECT user_id FROM bot_admins ORDER BY user_id"
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("❌ list_bot_admin_ids: %s", e)
            return []
