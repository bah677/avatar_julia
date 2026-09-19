"""
Mixin: платежи (`payments`) — создание/обновление/чтение, конвертация в RUB.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PaymentsMixin:

    async def create_payment(
        self,
        user_id: int,
        amount: float,
        payment_type: str,
        provider: str = "yookassa",
        provider_payment_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        user_telegram_data: Optional[str] = None,
        currency: str = "RUB",
        order_id: Optional[int] = None,
        provider_checkout_url: Optional[str] = None,
    ) -> Optional[int]:
        """Создаёт запись о платеже."""
        try:
            async with self.get_connection() as conn:
                payment_id = await conn.fetchval(
                    """
                    INSERT INTO payments
                    (user_id, amount, currency, payment_type, subscription_id,
                     payment_provider, provider_payment_id, status, user_telegram_data, order_id,
                     provider_checkout_url)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', $8, $9, $10)
                    RETURNING id
                    """,
                    user_id, amount, currency, payment_type, subscription_id,
                    provider, provider_payment_id, user_telegram_data, order_id,
                    provider_checkout_url,
                )
                logger.info(f"✅ Payment record created: id={payment_id}, user_id={user_id}, amount={amount}")
                return payment_id
        except Exception as e:
            logger.error(f"❌ Failed to create payment record: {e}")
            return None

    async def update_payment_status(
        self,
        payment_id: int,
        status: str,
        provider_payment_id: Optional[str] = None,
    ) -> bool:
        """Обновляет статус платежа (опционально проставляет provider_payment_id и completed_at)."""
        try:
            async with self.get_connection() as conn:
                query = "UPDATE payments SET status = $1, updated_at = NOW()"
                params: List[Any] = [status]

                if provider_payment_id:
                    query += f", provider_payment_id = ${len(params) + 1}"
                    params.append(provider_payment_id)

                if status == "succeeded":
                    query += ", completed_at = NOW()"

                query += f" WHERE id = ${len(params) + 1}"
                params.append(payment_id)

                await conn.execute(query, *params)
                logger.info(f"✅ Payment {payment_id} status updated to {status}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update payment status: {e}")
            return False

    async def get_payment(self, payment_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM payments WHERE id = $1",
                    payment_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get payment {payment_id}: {e}")
            return None

    async def get_pending_payments(self, after_date: datetime) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM payments
                    WHERE status = 'pending'
                      AND created_at >= $1
                    ORDER BY created_at ASC
                    """,
                    after_date,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get pending payments: {e}")
            return []

    async def update_payment_with_conversion(
        self,
        payment_id: int,
        rub_amount: float,
        exchange_rate: float,
    ) -> bool:
        """Сохраняет конвертированную в RUB сумму и курс."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE payments
                    SET amount_rub = $1,
                        converted_at = NOW(),
                        exchange_rate = $2
                    WHERE id = $3
                    """,
                    rub_amount, exchange_rate, payment_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update payment conversion: {e}")
            return False

    async def get_user_successful_payments_count(self, user_id: int) -> int:
        """Сколько успешных платежей было у пользователя."""
        try:
            async with self.get_connection() as conn:
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM payments
                    WHERE user_id = $1 AND status = 'succeeded'
                    """,
                    user_id,
                )
                return count or 0
        except Exception as e:
            logger.error(f"❌ Failed to get payments count: {e}")
            return 0

    async def count_successful_base_tariff_payments(self, user_id: int) -> int:
        """Успешные оплаты с тарифом type = 'base' (для реф-бонуса рефереру)."""
        try:
            async with self.get_connection() as conn:
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                      FROM payments p
                      JOIN orders o ON o.id = p.order_id
                      JOIN tariffs t ON t.id = o.tariff_id
                     WHERE p.user_id = $1
                       AND p.status = 'succeeded'
                       AND COALESCE(TRIM(t.type), '') = 'base'
                       AND COALESCE(o.is_gift, FALSE) = FALSE
                    """,
                    user_id,
                )
                return int(count or 0)
        except Exception as e:
            logger.error(f"❌ Failed to count base-tariff payments for user={user_id}: {e}")
            return 0

    async def try_finalize_pending_payment_success(
        self,
        payment_id: int,
        provider_payment_id: str,
        rub_amount: float,
        exchange_rate: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Атомарно: pending→succeeded, конвертация в RUB, заказ→paid.
        Возвращает строку платежа или None если уже не pending (идемпотентность).
        """
        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """
                        UPDATE payments
                           SET status = 'succeeded',
                               completed_at = NOW(),
                               updated_at = NOW(),
                               provider_payment_id = $2,
                               amount_rub = $3,
                               exchange_rate = $4,
                               converted_at = NOW()
                         WHERE id = $1 AND status = 'pending'
                         RETURNING *
                        """,
                        payment_id,
                        provider_payment_id,
                        rub_amount,
                        exchange_rate,
                    )
                    if not row:
                        return None

                    paid = dict(row)
                    oid = paid.get("order_id")
                    if oid is None:
                        logger.error(
                            "❌ try_finalize_pending_payment_success: missing order_id payment_id=%s",
                            payment_id,
                        )
                        return None

                    await conn.execute(
                        """
                        UPDATE orders
                           SET status = 'paid',
                               paid_at = NOW(),
                               amount_rub = $1
                         WHERE id = $2
                        """,
                        rub_amount,
                        oid,
                    )
                    return paid
        except Exception as e:
            logger.error(
                f"❌ try_finalize_pending_payment_success failed pid={payment_id}: {e}",
                exc_info=True,
            )
            return None

    async def subscription_delivery_audit_exists(self, payment_id: int) -> bool:
        """Повторная доставка подписки по этому payment_id уже писала аудит."""
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT EXISTS(
                      SELECT 1 FROM license_history
                       WHERE payment_id = $1
                         AND source = 'subscription_payment'
                    )
                    """,
                    payment_id,
                )
                return bool(val)
        except Exception as e:
            logger.error(f"❌ subscription_delivery_audit_exists pid={payment_id}: {e}")
            return False
