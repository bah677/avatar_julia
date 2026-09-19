"""
Mixin: тикеты поддержки (`support_tickets`).
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SupportMixin:

    async def create_support_ticket(self, user_id: int, topic: str, message: str) -> Optional[str]:
        """Создаёт тикет поддержки и возвращает его номер."""
        try:
            ticket_number = f"TKT_BB{uuid.uuid4().hex[:8].upper()}"
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO support_tickets
                    (user_id, ticket_number, topic, user_message, status, created_at)
                    VALUES ($1, $2, $3, $4, 'open', $5)
                    """,
                    user_id, ticket_number, topic, message, datetime.now(),
                )
            return ticket_number
        except Exception as e:
            logger.error(f"❌ Failed to create support ticket: {e}")
            return None

    async def get_user_tickets(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        ticket_number,
                        topic,
                        user_message,
                        admin_response,
                        status,
                        created_at,
                        updated_at
                    FROM support_tickets
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id, limit,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get user tickets: {e}")
            return []

    async def get_ticket_by_number(self, ticket_number: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        ticket_number,
                        topic,
                        user_message,
                        admin_response,
                        status,
                        created_at,
                        updated_at,
                        user_id
                    FROM support_tickets
                    WHERE ticket_number = $1
                    """,
                    ticket_number,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get ticket {ticket_number}: {e}")
            return None

    async def update_ticket_status(
        self,
        ticket_number: str,
        status: str,
        admin_id: Optional[int] = None,
        admin_response: Optional[str] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE support_tickets
                    SET status = $1, admin_id = $2, admin_response = $3, updated_at = NOW()
                    WHERE ticket_number = $4
                    """,
                    status, admin_id, admin_response, ticket_number,
                )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to update ticket status: {e}")
            return False
