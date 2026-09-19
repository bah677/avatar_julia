"""
Mixin: заказы (`orders`).
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OrdersMixin:

    async def create_order(
        self,
        user_id: int,
        tariff_id: int,
        currency: str,
        amount: float,
        is_gift: bool = False,
    ) -> Optional[int]:
        """Создаёт новый заказ (обычный или подарочный) со статусом 'pending'."""
        try:
            async with self.get_connection() as conn:
                order_id = await conn.fetchval(
                    """
                    INSERT INTO orders
                    (user_id, tariff_id, currency, amount, is_gift, status)
                    VALUES ($1, $2, $3, $4, $5, 'pending')
                    RETURNING id
                    """,
                    user_id, tariff_id, currency, amount, is_gift,
                )
                logger.info(
                    f"✅ Order created: id={order_id}, user_id={user_id}, "
                    f"amount={amount} {currency}, is_gift={is_gift}"
                )
                return order_id
        except Exception as e:
            logger.error(f"❌ Failed to create order: {e}")
            return None

    async def get_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает заказ + duration_days/tariff_name из tariffs."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT o.*,
                           t.duration_days,
                           t.name AS tariff_name,
                           t.type AS tariff_type
                    FROM orders o
                    JOIN tariffs t ON o.tariff_id = t.id
                    WHERE o.id = $1
                    """,
                    order_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get order {order_id}: {e}")
            return None

    async def update_order_paid(self, order_id: int, amount_rub: float) -> bool:
        """Помечает заказ как оплаченный и фиксирует сумму в рублях."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE orders
                    SET status = 'paid',
                        paid_at = NOW(),
                        amount_rub = $1
                    WHERE id = $2
                    """,
                    amount_rub, order_id,
                )
                logger.info(f"✅ Order {order_id} marked as paid")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update order {order_id}: {e}")
            return False
