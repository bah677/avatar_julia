"""
Mixin: тарифы (`tariffs` + `tariff_prices`).
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TariffsMixin:

    async def get_active_tariffs(self, tariff_type: str = "base") -> List[Dict[str, Any]]:
        """Активные тарифы заданного типа с массивом prices ([{currency, amount, old_amount}, ...])."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        t.id,
                        t.name,
                        t.duration_days,
                        t.type,
                        json_agg(
                            json_build_object(
                                'currency', tp.currency,
                                'amount', tp.amount,
                                'old_amount', tp.old_amount
                            )
                        ) as prices
                    FROM tariffs t
                    LEFT JOIN tariff_prices tp ON t.id = tp.tariff_id
                    WHERE t.active = TRUE AND t.type = $1
                    GROUP BY t.id, t.name, t.duration_days, t.type
                    ORDER BY t.duration_days
                    """,
                    tariff_type,
                )

                result: List[Dict[str, Any]] = []
                for row in rows:
                    tariff = dict(row)
                    if tariff["prices"] and isinstance(tariff["prices"], str):
                        tariff["prices"] = json.loads(tariff["prices"])
                    result.append(tariff)
                return result
        except Exception as e:
            logger.error(f"❌ Failed to get active tariffs: {e}")
            return []

    async def get_tariff_by_id(self, tariff_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        t.id,
                        t.name,
                        t.duration_days,
                        t.type,
                        json_agg(
                            json_build_object(
                                'currency', tp.currency,
                                'amount', tp.amount,
                                'old_amount', tp.old_amount
                            )
                        ) as prices
                    FROM tariffs t
                    LEFT JOIN tariff_prices tp ON t.id = tp.tariff_id
                    WHERE t.id = $1 AND t.active = TRUE
                    GROUP BY t.id, t.name, t.duration_days, t.type
                    """,
                    tariff_id,
                )
                if not row:
                    return None
                tariff = dict(row)
                if tariff["prices"] and isinstance(tariff["prices"], str):
                    tariff["prices"] = json.loads(tariff["prices"])
                return tariff
        except Exception as e:
            logger.error(f"❌ Failed to get tariff {tariff_id}: {e}")
            return None
