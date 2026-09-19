"""
Тонкий фасад поверх Database с привычным именем `UserStorage`.

Вся низкоуровневая работа с PostgreSQL живёт в `storage/db/*` — миксинах
по предметным областям (users, messages, payments, licenses, orders,
tariffs, referrals, support, gifts).

Здесь оставлены только:
- `initialize()` — открыть пул подключений с понятным именем;
- небольшие удобные обёртки с альтернативными именами/сигнатурами,
  которыми исторически пользуются прикладные модули (фичи, хендлеры,
  middleware) — чтобы не править их все при чистке слоя данных.
"""

import logging
from typing import Optional

from storage.db.database import Database

logger = logging.getLogger(__name__)


class UserStorage(Database):
    """Совместимое имя для основной точки работы с PostgreSQL.

    Раньше существовала пара ``UserStorage`` ↔ внутренний объект ``Database``,
    к которому ходили как ``user_storage.db.get_connection()`` /
    ``user_storage.db.pool``. После рефакторинга UserStorage сам и есть
    Database, поэтому атрибут ``db`` указывает на ``self`` — это
    обратносовместимый shim, чтобы прикладные модули (features, middleware,
    media-processors) продолжали работать без правок.
    """

    @property
    def db(self) -> "UserStorage":
        return self

    async def initialize(self) -> None:
        """Открывает пул подключений (асинхронный)."""
        await self.connect()
        logger.info("✅ UserStorage initialized")

    # =====================================================
    # Удобные обёртки с историческими именами
    # =====================================================

    async def save_user_from_message(self, message) -> bool:
        """Создаёт/обновляет пользователя из объекта `aiogram.types.Message`."""
        user = message.from_user
        return await self.add_or_update_user(
            {
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "is_premium": getattr(user, "is_premium", False),
            }
        )

    async def get_thread_id(self, user_id: int) -> Optional[str]:
        """openai_thread_id пользователя (если есть)."""
        user = await self.get_user(user_id)
        return user.get("openai_thread_id") if user else None

    async def save_thread_id(self, user_id: int, thread_id: str) -> bool:
        return await self.update_openai_thread(user_id, thread_id)

    async def update_activity(self, user_id: int) -> bool:
        return await self.update_user_activity(user_id)

    async def log_message(
        self,
        user_id: int,
        message_text: str,
        message_type: str,
        thread_id: Optional[str] = None,
        assistant_id: Optional[str] = None,
    ) -> bool:
        """Совместимая обёртка над add_message."""
        return await self.add_message(
            user_id, message_text, message_type, thread_id, None, assistant_id
        )

    async def add_license(self, user_id: int) -> bool:
        return await self.add_user_license(user_id)

    async def check_license(self, user_id: int) -> bool:
        return await self.check_user_license(user_id)

    async def complete_onboarding(self, user_id: int) -> bool:
        return await self.set_onboarding_complete(user_id)

    # =====================================================
    # Рефералы — высокоуровневая бизнес-логика поверх миксина
    # =====================================================

    async def process_referral(self, user_id: int, referrer_id: int) -> bool:
        """Регистрирует реферал. Если у пользователя уже есть реферер — пропускает."""
        try:
            existing = await self.get_referral_by_referred_id(str(user_id))
            if existing:
                logger.info(
                    f"User {user_id} already has a referrer "
                    f"(id={existing['referrer_id']}), skipping new referral"
                )
                return False

            success = await self.create_referral(referrer_id, str(user_id))
            if success:
                logger.info(
                    f"✅ Referral registered: referrer={referrer_id}, referred={user_id}"
                )
            return success
        except Exception as e:
            logger.error(
                f"❌ Failed to process referral for user_id={user_id}, "
                f"referrer_id={referrer_id}: {e}"
            )
            return False

    async def get_referrer_for_user(self, user_id: int) -> Optional[int]:
        return await self.get_referrer_by_referred(str(user_id))

    async def has_referrer(self, user_id: int) -> bool:
        return (await self.get_referrer_for_user(user_id)) is not None
