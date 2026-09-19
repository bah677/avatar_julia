"""
Mixin: работа с пользователями (таблица `users`).
Счётчик заданных вопросов, онбординг, профиль/ДР, базовый CRUD,
лицензионная отметка (полная работа с лицензиями в LicensesMixin).
"""

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UsersMixin:

    # =====================================================
    # Счётчик использованных вопросов
    # =====================================================

    async def get_user_usage_stats(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает счётчик заданных вопросов."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT questions_asked FROM users WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get usage stats for user_id={user_id}: {e}")
            return None

    async def increment_questions_asked(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET questions_asked = questions_asked + 1 WHERE user_id = $1",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to increment questions for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Лицензионная отметка (использует таблицу license, но смысл — на пользователе)
    # Полная работа с лицензиями в LicensesMixin.
    # =====================================================

    async def check_user_license(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.fetchval(
                    "SELECT 1 FROM license WHERE user_id = $1 AND status = $2",
                    user_id, "active",
                )
                return result is not None
        except Exception as e:
            logger.error(f"❌ Failed to check license for user_id={user_id}: {e}")
            return False

    async def add_user_license(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "INSERT INTO license (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to add license for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Онбординг
    # =====================================================

    async def set_onboarding_complete(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET onboarding_complete = TRUE WHERE user_id = $1",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to set onboarding complete for user_id={user_id}: {e}")
            return False

    async def is_onboarding_complete(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.fetchval(
                    "SELECT onboarding_complete FROM users WHERE user_id = $1",
                    user_id,
                )
                return bool(result)
        except Exception as e:
            logger.error(f"❌ Failed to check onboarding status for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Базовый CRUD пользователей
    # =====================================================

    async def add_or_update_user(self, user_data: Dict[str, Any]) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO users
                    (user_id, username, first_name, last_name, language_code, is_premium, last_activity)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        language_code = EXCLUDED.language_code,
                        is_premium = EXCLUDED.is_premium,
                        last_activity = EXCLUDED.last_activity,
                        is_active = TRUE
                    """,
                    user_data["user_id"],
                    user_data.get("username"),
                    user_data.get("first_name"),
                    user_data.get("last_name"),
                    user_data.get("language_code"),
                    bool(user_data.get("is_premium")),
                    datetime.now(),
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to save user {user_data.get('user_id')}: {e}")
            return False

    async def update_openai_thread(self, user_id: int, thread_id: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET openai_thread_id = $1 WHERE user_id = $2",
                    thread_id, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update thread for user_id={user_id}: {e}")
            return False

    async def update_user_activity(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET last_activity = $1 WHERE user_id = $2",
                    datetime.now(), user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update activity for user_id={user_id}: {e}")
            return False

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get user {user_id}: {e}")
            return None

    async def get_all_users(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get users: {e}")
            return []

    async def get_active_users(self, days: int = 30) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                # Используем параметр через make_interval, чтобы не подставлять через f-строку
                rows = await conn.fetch(
                    """
                    SELECT * FROM users
                    WHERE last_activity >= NOW() - make_interval(days => $1)
                    ORDER BY last_activity DESC
                    """,
                    days,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get active users: {e}")
            return []

    # =====================================================
    # Профиль / дата рождения / таймзона / сброс / рейтинг
    # =====================================================

    async def update_user_birthday(self, user_id: int, birthday: date) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET bd = $1 WHERE user_id = $2",
                    birthday, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update birthday for user_id={user_id}: {e}")
            return False

    async def update_user_profile(self, user_id: int, profile: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET profile = $1 WHERE user_id = $2",
                    profile, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update profile for user_id={user_id}: {e}")
            return False

    async def get_user_birthday(self, user_id: int) -> Optional[date]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT bd FROM users WHERE user_id = $1",
                    user_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to get birthday for user_id={user_id}: {e}")
            return None

    async def get_user_profile(self, user_id: int) -> Optional[str]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT profile FROM users WHERE user_id = $1",
                    user_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to get profile for user_id={user_id}: {e}")
            return None

    async def reset_user_data(self, user_id: int) -> bool:
        """Сбрасывает данные пользователя для повторного онбординга (сохраняет историю)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET onboarding_complete = FALSE,
                        bd = NULL,
                        profile = NULL,
                        openai_thread_id = NULL,
                        last_activity = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info(f"✅ User onboarding data reset for user_id={user_id}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to reset user data for user_id={user_id}: {e}")
            return False

    async def save_first_answer_rating(self, user_id: int, rating: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET first_answer_rating = $1, rated_at = NOW()
                    WHERE user_id = $2
                    """,
                    rating, user_id,
                )
                logger.info(f"✅ Rating saved for user_id={user_id}: {rating}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to save rating for user_id={user_id}: {e}")
            return False

    async def update_user_timezone(self, user_id: int, timezone_offset: int) -> bool:
        """Обновляет часовой пояс пользователя (в минутах от UTC)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET timezone_offset = $1 WHERE user_id = $2",
                    timezone_offset, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update timezone for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Бан / деактивация
    # =====================================================

    async def is_user_banned(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.fetchval(
                    "SELECT is_banned FROM users WHERE user_id = $1",
                    user_id,
                )
                return bool(result)
        except Exception as e:
            logger.error(f"❌ Failed to check ban for user {user_id}: {e}")
            return False

    async def deactivate_user(self, user_id: int) -> bool:
        """Помечает пользователя как неактивного (например, заблокировал бота)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET is_active = FALSE, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info(f"✅ User {user_id} deactivated (blocked bot)")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to deactivate user {user_id}: {e}")
            return False

    # =====================================================
    # Сессия Agents (DeepSeek/AgentsClient)
    # =====================================================

    async def save_agent_session_id(self, user_id: int, session_id: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET agent_session_id = $1 WHERE user_id = $2",
                    session_id, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to save agent session for user {user_id}: {e}")
            return False

    async def get_agent_session_id(self, user_id: int) -> Optional[str]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT agent_session_id FROM users WHERE user_id = $1",
                    user_id,
                )
                return row["agent_session_id"] if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get agent session for user {user_id}: {e}")
            return None

    async def clear_agent_session_id(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET agent_session_id = NULL WHERE user_id = $1",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to clear agent session for user {user_id}: {e}")
            return False

    # =====================================================
    # Донат после рассылки / показ кнопки (legacy Biblia)
    # =====================================================

    async def set_show_donation_flag(self, user_id: int, value: bool = True) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET show_donation_on_next_response = $1
                    WHERE user_id = $2
                    """,
                    value,
                    user_id,
                )
                return True
        except Exception as e:
            logger.error("❌ Failed to set show donation flag for user %s: %s", user_id, e)
            return False

    async def get_and_clear_show_donation_flag(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                current = await conn.fetchval(
                    """
                    SELECT show_donation_on_next_response FROM users WHERE user_id = $1
                    """,
                    user_id,
                )
                if current:
                    await conn.execute(
                        """
                        UPDATE users
                        SET show_donation_on_next_response = FALSE
                        WHERE user_id = $1
                        """,
                        user_id,
                    )
                return bool(current)
        except Exception as e:
            logger.error("❌ Failed to get/clear show donation flag for user %s: %s", user_id, e)
            return False

    async def increment_donation_button_counter(self, user_id: int) -> bool:
        """Счётчик показов инлайн-кнопки поддержки под ответом ассистента."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET donation_button = COALESCE(donation_button, 0) + 1
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                return True
        except Exception as e:
            logger.error("❌ Failed to increment donation_button for user %s: %s", user_id, e)
            return False
