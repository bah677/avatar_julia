"""
Mixin: рефералы (`referrals`, `ref_keys`) + бонусы и источник перехода.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ReferralsMixin:

    async def get_referral_by_referred_id(self, referred_id: str) -> Optional[Dict[str, Any]]:
        """Запись о реферале по ID реферала (Telegram ID как строка)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM referrals WHERE referred_id = $1",
                    referred_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get referral by referred_id={referred_id}: {e}")
            return None

    async def get_referral_by_referrer_and_referred(
        self,
        referrer_id: int,
        referred_id: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM referrals WHERE referrer_id = $1 AND referred_id = $2",
                    referrer_id, referred_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"❌ Failed to get referral by referrer={referrer_id}, "
                f"referred={referred_id}: {e}"
            )
            return None

    async def create_referral(self, referrer_id: int, referred_id: str) -> bool:
        """True только если вставлена новая строка (не конфликт)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO referrals (referrer_id, referred_id, created_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT DO NOTHING
                    RETURNING referred_id
                    """,
                    referrer_id,
                    referred_id,
                )
                if row:
                    logger.info(
                        "✅ Referral created: referrer_id=%s, referred_id=%s",
                        referrer_id,
                        referred_id,
                    )
                    return True
                logger.info(
                    "Referral insert skipped (duplicate): referrer_id=%s, referred_id=%s",
                    referrer_id,
                    referred_id,
                )
                return False
        except Exception as e:
            logger.error(f"❌ Failed to create referral: {e}")
            return False

    async def get_referrer_by_referred(self, referred_id: str) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT referrer_id FROM referrals WHERE referred_id = $1",
                    referred_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to get referrer for referred_id={referred_id}: {e}")
            return None

    async def get_referrals_count(self, referrer_id: int) -> int:
        try:
            async with self.get_connection() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM referrals WHERE referrer_id = $1",
                    referrer_id,
                )
                return count or 0
        except Exception as e:
            logger.error(f"❌ Failed to get referrals count for referrer_id={referrer_id}: {e}")
            return 0

    async def get_referral_stats(self, referrer_id: int) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                total = await self.get_referrals_count(referrer_id)
                monthly = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM referrals
                    WHERE referrer_id = $1
                      AND created_at >= NOW() - INTERVAL '30 days'
                    """,
                    referrer_id,
                ) or 0
                paid = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT r.referred_id)
                      FROM referrals r
                      JOIN payments p ON p.user_id::text = r.referred_id
                     WHERE r.referrer_id = $1 AND p.status = 'succeeded'
                    """,
                    referrer_id,
                ) or 0
                bonuses = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                      FROM referrals
                     WHERE referrer_id = $1 AND bonus_granted = TRUE
                    """,
                    referrer_id,
                ) or 0
                recent = await conn.fetch(
                    """
                    SELECT referred_id, created_at
                    FROM referrals
                    WHERE referrer_id = $1
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    referrer_id,
                )
                return {
                    "total": total,
                    "monthly": monthly,
                    "paid": int(paid),
                    "bonuses_given": int(bonuses),
                    "recent": [dict(row) for row in recent],
                }
        except Exception as e:
            logger.error(f"❌ Failed to get referral stats for referrer_id={referrer_id}: {e}")
            return {
                "total": 0,
                "monthly": 0,
                "paid": 0,
                "bonuses_given": 0,
                "recent": [],
            }

    async def get_referrals_list(
        self, referrer_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Список рефералов с расширенной информацией о пользователе."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        r.referred_id,
                        r.created_at AS referral_date,
                        r.bonus_granted AS referral_bonus_unlocked,
                        u.username,
                        u.first_name,
                        u.last_name,
                        u.created_at AS user_created_at,
                        u.onboarding_complete,
                        u.last_activity,
                        EXISTS (
                          SELECT 1 FROM payments p
                           WHERE p.user_id::text = r.referred_id
                             AND p.status = 'succeeded'
                        ) AS has_paid
                    FROM referrals r
                    LEFT JOIN users u ON u.user_id::text = r.referred_id
                    WHERE r.referrer_id = $1
                    ORDER BY r.created_at DESC
                    LIMIT $2
                    """,
                    referrer_id, limit,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"❌ Failed to get referrals list for referrer_id={referrer_id}: {e}"
            )
            return []

    async def get_referrer_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Информация о реферере данного пользователя."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT r.referrer_id, r.bonus_granted, u.first_name, u.last_name
                    FROM referrals r
                    JOIN users u ON r.referrer_id = u.user_id
                    WHERE r.referred_id = $1
                    """,
                    str(user_id),
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get referrer info: {e}")
            return None

    async def mark_referral_bonus_granted(self, user_id: int) -> bool:
        """Бонус рефереру выдан за первую оплату этого реферала: фиксируем в строке referrals и в users."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE referrals
                       SET bonus_granted = TRUE
                     WHERE referred_id = $1
                    """,
                    str(user_id),
                )
                await conn.execute(
                    """
                    UPDATE users
                       SET referral_bonus_granted = TRUE
                     WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info(
                    f"✅ Referral bonus marked as granted for referred user_id={user_id}"
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to mark referral bonus for user {user_id}: {e}")
            return False

    async def get_last_referral_source(self, user_id: int) -> Optional[str]:
        """Достаёт из interaction_logs последний `/start ref_<key>` пользователя."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT data->>'text' as text
                    FROM interaction_logs
                    WHERE user_id = $1
                      AND event_category = 'message'
                      AND data->>'text' LIKE '/start ref_%'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                if not row or not row["text"]:
                    return None

                match = re.search(r"ref_([a-zA-Z0-9]+)", row["text"])
                if not match:
                    return None
                ref_key = match.group(1)

                name_row = await conn.fetchrow(
                    "SELECT name FROM ref_keys WHERE ref_key = $1",
                    ref_key,
                )
                return name_row["name"] if name_row else ref_key
        except Exception as e:
            logger.error(f"❌ Failed to get referral source for user {user_id}: {e}")
            return None
