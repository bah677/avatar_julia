"""
Mixin: подарочные подписки (`gifts`).
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class GiftsMixin:

    async def create_gift(
        self,
        order_id: int,
        user_id: int,
        tariff_id: int,
        gift_code: str,
        expires_at: datetime,
    ) -> bool:
        """Создаёт запись о подарке."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO gifts (order_id, user_id, tariff_id, gift_code, expires_at)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    order_id, user_id, tariff_id, gift_code, expires_at,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to create gift: {e}")
            return False

    async def get_gift_by_code(self, gift_code: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM gifts WHERE gift_code = $1",
                    gift_code,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get gift by code: {e}")
            return None

    async def update_gift_status(
        self,
        gift_code: str,
        status: str,
        activated_by: Optional[int] = None,
        activated_at: Optional[datetime] = None,
    ) -> bool:
        """Меняет статус подарка (например, activated)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE gifts
                    SET status = $1, activated_by = $2, activated_at = $3
                    WHERE gift_code = $4
                    """,
                    status, activated_by, activated_at, gift_code,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update gift status: {e}")
            return False

    async def get_gift_by_order_id(self, order_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM gifts WHERE order_id = $1 LIMIT 1",
                    order_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get gift by order_id={order_id}: {e}")
            return None
