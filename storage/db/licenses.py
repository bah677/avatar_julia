"""
Mixin: лицензии (`license`).
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LicensesMixin:

    async def user_has_active_license(self, user_id: int) -> bool:
        """True, если у пользователя есть неистёкшая активная лицензия (как get_user_active_license)."""
        row = await self.get_user_active_license(user_id)
        return row is not None

    async def get_user_license_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает активную лицензию (с amount/created_at из payments).

        Если срок истёк — переводит в 'expired' и возвращает None.
        """
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        l.*,
                        p.amount,
                        p.created_at as payment_date
                    FROM license l
                    LEFT JOIN payments p ON l.payment_id = p.id
                    WHERE l.user_id = $1 AND l.status = 'active'
                    ORDER BY l.created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )

                if not row:
                    return None

                license_info = dict(row)
                expires_at = license_info.get("expires_at")
                if expires_at and expires_at < datetime.now():
                    did = await conn.fetchval(
                        """
                        UPDATE license
                           SET status = 'expired', updated_at = NOW()
                         WHERE user_id = $1 AND status = 'active'
                        RETURNING 1
                        """,
                        user_id,
                    )
                    if did is not None:
                        await self.append_license_history(
                            user_id=user_id,
                            previous_expires_at=expires_at,
                            new_expires_at=expires_at,
                            source="expired_detected_on_read",
                            meta={"status_change": "active_to_expired"},
                        )
                    return None

                return license_info
        except Exception as e:
            logger.error(f"❌ Failed to get license info for user_id={user_id}: {e}")
            return None

    async def get_expiring_licenses(self, days_before: int = 3) -> List[Dict[str, Any]]:
        """Активные лицензии, истекающие в ближайшие days_before дней."""
        try:
            async with self.get_connection() as conn:
                expiration_date = datetime.now() + timedelta(days=days_before)
                rows = await conn.fetch(
                    """
                    SELECT
                        l.*,
                        u.username,
                        u.first_name,
                        u.last_name,
                        p.amount,
                        p.payment_type
                    FROM license l
                    JOIN users u ON l.user_id = u.user_id
                    LEFT JOIN payments p ON l.payment_id = p.id
                    WHERE l.status = 'active'
                      AND l.expires_at BETWEEN NOW() AND $1
                    ORDER BY l.expires_at ASC
                    """,
                    expiration_date,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expiring licenses: {e}")
            return []

    async def get_user_active_license(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Активная (не истекшая) лицензия пользователя."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM license
                    WHERE user_id = $1 AND status = 'active' AND expires_at > NOW()
                    ORDER BY expires_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get active license for user {user_id}: {e}")
            return None

    async def list_user_ids_with_active_license(self) -> List[int]:
        """Все user_id с неистёкшей активной лицензией (включая бонусные продления)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT user_id FROM license
                    WHERE status = 'active' AND expires_at > NOW()
                    ORDER BY user_id
                    """
                )
                return [r["user_id"] for r in rows]
        except Exception as e:
            logger.error(f"❌ Failed to list active license user ids: {e}")
            return []

    async def append_license_history(
        self,
        user_id: int,
        previous_expires_at: Optional[Any],
        new_expires_at: Any,
        source: str,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        referred_user_id: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            import json as _json
            async with self.get_connection() as conn:
                if meta is not None:
                    await conn.execute(
                        """
                        INSERT INTO license_history
                            (user_id, previous_expires_at, new_expires_at, source,
                             order_id, payment_id, referred_user_id, meta)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                        """,
                        user_id,
                        previous_expires_at,
                        new_expires_at,
                        source,
                        order_id,
                        payment_id,
                        referred_user_id,
                        _json.dumps(meta),
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO license_history
                            (user_id, previous_expires_at, new_expires_at, source,
                             order_id, payment_id, referred_user_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        user_id,
                        previous_expires_at,
                        new_expires_at,
                        source,
                        order_id,
                        payment_id,
                        referred_user_id,
                    )
        except Exception as e:
            logger.error(f"❌ append_license_history user={user_id}: {e}", exc_info=True)

    async def create_or_extend_license(
        self,
        user_id: int,
        order_id: int,
        expires_at: datetime,
        license_type: str = "subscription",
        *,
        audit_source: str = "subscription_payment",
        audit_payment_id: Optional[int] = None,
        audit_order_id: Optional[int] = None,
    ) -> bool:
        """Создаёт или обновляет лицензию пользователя.

        У каждого пользователя — одна запись в `license`; обновляем expires_at и статус.
        """
        try:
            prev_expires: Optional[datetime] = None
            async with self.get_connection() as conn:
                row_prev = await conn.fetchrow(
                    "SELECT expires_at FROM license WHERE user_id = $1",
                    user_id,
                )
                if row_prev:
                    prev_expires = row_prev["expires_at"]

                existing = await conn.fetchrow(
                    "SELECT id FROM license WHERE user_id = $1",
                    user_id,
                )
                if existing:
                    await conn.execute(
                        """
                        UPDATE license
                        SET expires_at = $1,
                            status = 'active',
                            license_type = $2,
                            payment_id = $3,
                            updated_at = NOW()
                        WHERE user_id = $4
                        """,
                        expires_at,
                        license_type,
                        order_id,
                        user_id,
                    )
                    logger.info(f"✅ License updated for user {user_id} until {expires_at}")
                else:
                    await conn.execute(
                        """
                        INSERT INTO license
                        (user_id, license_type, expires_at, payment_id, status)
                        VALUES ($1, $2, $3, $4, 'active')
                        """,
                        user_id,
                        license_type,
                        expires_at,
                        order_id,
                    )
                    logger.info(f"✅ New license created for user {user_id} until {expires_at}")

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=prev_expires,
                new_expires_at=expires_at,
                source=audit_source,
                order_id=audit_order_id if audit_order_id is not None else order_id,
                payment_id=audit_payment_id,
                meta={"license_type": license_type},
            )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to create/extend license for user {user_id}: {e}")
            return False

    # =====================================================
    # Бонусные продления / истечения / конвертация
    # =====================================================

    async def get_user_active_license_subscription(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        """Активная лицензия типа 'subscription' (без bonus_extension)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM license
                    WHERE user_id = $1
                      AND status = 'active'
                      AND license_type = 'subscription'
                      AND expires_at > NOW()
                    ORDER BY expires_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get active subscription for user {user_id}: {e}")
            return None

    async def extend_license_by_days(
        self,
        user_id: int,
        days: int,
        *,
        audit_referred_user_id: Optional[int] = None,
    ) -> bool:
        """Продлевает (или создаёт) лицензию на `days` дней (тип 'bonus')."""
        try:
            now = datetime.now()
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT expires_at
                      FROM license
                     WHERE user_id = $1 AND status = 'active'
                     ORDER BY expires_at DESC NULLS LAST
                     LIMIT 1
                    """,
                    user_id,
                )
                prev_expires = row["expires_at"] if row else None

                if row and row["expires_at"] and row["expires_at"] > now:
                    new_expiry = row["expires_at"] + timedelta(days=days)
                    logger.info(
                        f"📅 Extending license for user {user_id} from "
                        f"{row['expires_at']} to {new_expiry}"
                    )
                    await conn.execute(
                        """
                        UPDATE license
                           SET expires_at = $1,
                               updated_at = NOW()
                         WHERE user_id = $2 AND status = 'active'
                        """,
                        new_expiry,
                        user_id,
                    )
                else:
                    new_expiry = now + timedelta(days=days)
                    logger.info(
                        f"📅 Creating new license for user {user_id} until {new_expiry}"
                    )
                    await conn.execute(
                        """
                        INSERT INTO license (user_id, license_type, expires_at, status)
                        VALUES ($1, 'bonus', $2, 'active')
                        """,
                        user_id,
                        new_expiry,
                    )

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=prev_expires,
                new_expires_at=new_expiry,
                source="referral_bonus",
                referred_user_id=audit_referred_user_id,
                meta={"days_added": days},
            )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to extend license: {e}")
            return False

    async def get_active_subscriptions_expiring_on(
        self, target_date: "date"
    ) -> List[Dict[str, Any]]:
        """Активные подписки, истекающие в указанную дату (±1 день)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT l.user_id, l.expires_at
                    FROM license l
                    JOIN users u ON l.user_id = u.user_id
                    WHERE l.status = 'active'
                      AND l.license_type = 'subscription'
                      AND DATE(l.expires_at) <= $1::date + interval '1 day'
                      AND DATE(l.expires_at) > $1::date - interval '1 day'
                      AND u.is_active = TRUE
                    """,
                    target_date,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expiring subscriptions: {e}")
            return []

    async def get_expired_subscriptions_for_bonus(
        self, target_date: "date"
    ) -> List[Dict[str, Any]]:
        """Подписки, истёкшие в указанную дату — кандидаты на бонусное продление."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM license
                    WHERE status = 'active'
                      AND license_type = 'subscription'
                      AND DATE(expires_at) = $1
                    """,
                    target_date,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expired subscriptions: {e}")
            return []

    async def convert_to_bonus_license(
        self, user_id: int, new_expiry: datetime
    ) -> bool:
        """Конвертирует подписку в бонусное продление."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT expires_at, license_type
                      FROM license
                     WHERE user_id = $1 AND status = 'active'
                     LIMIT 1
                    """,
                    user_id,
                )
                if not row:
                    logger.warning(
                        "convert_to_bonus_license: нет активной лицензии uid=%s", user_id
                    )
                    return False

                prev_expires = row["expires_at"]
                prior_type = row.get("license_type")

                await conn.execute(
                    """
                    UPDATE license
                    SET expires_at = $2,
                        license_type = 'bonus_extension',
                        updated_at = NOW()
                    WHERE user_id = $1 AND status = 'active'
                    """,
                    user_id,
                    new_expiry,
                )

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=prev_expires,
                new_expires_at=new_expiry,
                source="bonus_extension_offer",
                meta=(
                    {"prior_license_type": prior_type}
                    if prior_type is not None
                    else None
                ),
            )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to convert to bonus license: {e}")
            return False

    async def get_expired_bonus_licenses(
        self, target_date: "date"
    ) -> List[Dict[str, Any]]:
        """Истёкшие в указанную дату бонусные лицензии (для удаления из группы)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM license
                    WHERE status = 'active'
                      AND license_type = 'bonus_extension'
                      AND DATE(expires_at) = $1
                    """,
                    target_date,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expired bonus licenses: {e}")
            return []

    async def mark_license_expired(self, user_id: int) -> bool:
        """Помечает активную лицензию пользователя как истёкшую."""
        prev_expires: Optional[datetime] = None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT expires_at FROM license
                     WHERE user_id = $1 AND status = 'active'
                     LIMIT 1
                    """,
                    user_id,
                )
                if not row:
                    return True

                prev_expires = row["expires_at"]

                await conn.execute(
                    """
                    UPDATE license
                       SET status = 'expired', updated_at = NOW()
                     WHERE user_id = $1 AND status = 'active'
                    """,
                    user_id,
                )

            if prev_expires is not None:
                await self.append_license_history(
                    user_id=user_id,
                    previous_expires_at=prev_expires,
                    new_expires_at=prev_expires,
                    source="subscription_expired",
                    meta={"status_change": "active_to_expired"},
                )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to mark license expired: {e}")
            return False
