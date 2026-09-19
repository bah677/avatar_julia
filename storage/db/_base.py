"""
Базовый слой Database: пул подключений к PostgreSQL.
Все mixin'ы используют self.get_connection() из этого класса.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


class DatabaseBase:
    """Управление пулом подключений к PostgreSQL."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """Создаёт пул подключений к базе данных."""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=5,
                max_size=20,
                command_timeout=60,
            )
            logger.info("✅ PostgreSQL connection pool created successfully")
        except Exception as e:
            logger.error(f"❌ Failed to connect to PostgreSQL: {e}")
            raise

    async def close(self) -> None:
        """Закрывает пул подключений."""
        if self.pool:
            await self.pool.close()
            logger.info("✅ PostgreSQL connection pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """Контекстный менеджер для получения подключения из пула."""
        if not self.pool:
            raise RuntimeError("Database not connected")
        async with self.pool.acquire() as connection:
            yield connection
