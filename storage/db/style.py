"""Mixin: паспорт голоса и настройки контента."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class StyleMixin:
    async def get_active_style_profile(self, product_id: str) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM style_profiles
                 WHERE product_id = $1 AND is_active = TRUE
                 ORDER BY version DESC
                 LIMIT 1
                """,
                product_id,
            )
        return dict(row) if row else None

    async def insert_style_profile(
        self,
        *,
        product_id: str,
        text: str,
        origin: str,
        source_hash: str = "",
        created_by: Optional[int] = None,
        activate: bool = True,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    ver = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(version), 0) + 1
                          FROM style_profiles WHERE product_id = $1
                        """,
                        product_id,
                    )
                    if activate:
                        await conn.execute(
                            """
                            UPDATE style_profiles SET is_active = FALSE
                             WHERE product_id = $1 AND is_active = TRUE
                            """,
                            product_id,
                        )
                    row = await conn.fetchrow(
                        """
                        INSERT INTO style_profiles (
                            product_id, version, text, origin, source_hash,
                            is_active, created_by
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                        RETURNING id
                        """,
                        product_id,
                        int(ver),
                        text or "",
                        origin,
                        source_hash or "",
                        bool(activate),
                        created_by,
                    )
                return int(row["id"]) if row else None
        except Exception as e:
            logger.error("insert_style_profile: %s", e)
            return None

    async def append_style_rules(self, product_id: str, rules_md: str) -> bool:
        """Дописывает правила в раздел 8 активного паспорта, не трогая остальное."""
        row = await self.get_active_style_profile(product_id)
        if not row:
            return False
        body = row.get("text") or ""
        marker = "## 8."
        extra = (rules_md or "").strip()
        if not extra:
            return True
        if marker in body:
            body = body.rstrip() + "\n\n" + extra + "\n"
        else:
            body = body.rstrip() + "\n\n## 8. Правила из обратной связи\n\n" + extra + "\n"
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE style_profiles SET text = $2 WHERE id = $1",
                    row["id"],
                    body,
                )
            return True
        except Exception as e:
            logger.error("append_style_rules: %s", e)
            return False

    async def get_content_setting(
        self, product_id: str, key: str
    ) -> Optional[Any]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT value FROM content_settings
                 WHERE product_id = $1 AND key = $2
                """,
                product_id,
                key,
            )
        if not row:
            return None
        return row["value"]

    async def set_content_setting(
        self, product_id: str, key: str, value: Any
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO content_settings (product_id, key, value, updated_at)
                VALUES ($1, $2, $3::jsonb, NOW())
                ON CONFLICT (product_id, key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW()
                """,
                product_id,
                key,
                json.dumps(value, ensure_ascii=False),
            )
