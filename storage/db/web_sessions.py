"""Mixin: коды входа и сессии веб-студии."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WebSessionsMixin:
    # ── Коды входа ─────────────────────────────────────────────────────────
    async def insert_web_login_code(
        self, *, user_id: int, code_hash: str, ttl_sec: int = 600
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    """
                    INSERT INTO web_login_codes (user_id, code_hash, expires_at)
                    VALUES ($1, $2, NOW() + make_interval(secs => $3))
                    RETURNING id
                    """,
                    int(user_id),
                    code_hash,
                    float(ttl_sec),
                )
        except Exception as e:
            logger.error("insert_web_login_code: %s", e)
            return None

    async def count_recent_web_login_codes(self, *, user_id: int, window_sec: int = 900) -> int:
        async with self.get_connection() as conn:
            return int(
                await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM web_login_codes
                     WHERE user_id = $1
                       AND created_at > NOW() - make_interval(secs => $2)
                    """,
                    int(user_id),
                    float(window_sec),
                )
                or 0
            )

    async def get_active_web_login_code(self, *, user_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM web_login_codes
                 WHERE user_id = $1 AND used_at IS NULL AND expires_at > NOW()
                 ORDER BY id DESC
                 LIMIT 1
                """,
                int(user_id),
            )
        return dict(row) if row else None

    async def bump_web_login_attempts(self, code_id: int) -> int:
        async with self.get_connection() as conn:
            return int(
                await conn.fetchval(
                    """
                    UPDATE web_login_codes SET attempts = attempts + 1
                     WHERE id = $1
                     RETURNING attempts
                    """,
                    int(code_id),
                )
                or 0
            )

    async def mark_web_login_code_used(self, code_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE web_login_codes SET used_at = NOW() WHERE id = $1", int(code_id)
            )

    async def cleanup_web_login_codes(self) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                "DELETE FROM web_login_codes WHERE expires_at < NOW() - INTERVAL '1 day'"
            )

    # ── Сессии ─────────────────────────────────────────────────────────────
    async def insert_web_session(
        self,
        *,
        token_hash: str,
        user_id: int,
        user_agent: str = "",
        ip: str = "",
        ttl_days: int = 30,
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO web_sessions (token_hash, user_id, user_agent, ip, expires_at)
                VALUES ($1, $2, $3, $4, NOW() + make_interval(days => $5))
                ON CONFLICT (token_hash) DO UPDATE
                   SET last_seen_at = NOW(), expires_at = EXCLUDED.expires_at
                """,
                token_hash,
                int(user_id),
                (user_agent or "")[:300],
                (ip or "")[:64],
                float(ttl_days),
            )

    async def get_web_session(self, token_hash: str) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM web_sessions
                 WHERE token_hash = $1 AND expires_at > NOW()
                """,
                token_hash,
            )
        return dict(row) if row else None

    async def touch_web_session(self, token_hash: str) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE web_sessions SET last_seen_at = NOW() WHERE token_hash = $1",
                token_hash,
            )

    async def delete_web_session(self, token_hash: str) -> None:
        async with self.get_connection() as conn:
            await conn.execute("DELETE FROM web_sessions WHERE token_hash = $1", token_hash)

    async def delete_web_sessions_for_user(self, user_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute("DELETE FROM web_sessions WHERE user_id = $1", int(user_id))

    async def list_web_sessions(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM web_sessions
                 WHERE expires_at > NOW()
                 ORDER BY last_seen_at DESC
                 LIMIT $1
                """,
                int(limit),
            )
        return [dict(r) for r in rows]
