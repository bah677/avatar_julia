"""
Фича реферальной программы.
Позволяет пользователям получать свою реферальную ссылку.
Регистрирует рефералов только для новых пользователей и только один раз.
"""

import html
import logging
from datetime import datetime
from typing import Any, Dict

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.utils.telegram_identity import resolve_telegram_bot_username

logger = logging.getLogger(__name__)


def _invite_display_name(row: Dict[str, Any]) -> str:
    fn = (row.get("first_name") or "").strip()
    ln = (row.get("last_name") or "").strip()
    un = (row.get("username") or "").strip()
    full = (fn + " " + ln).strip()
    if full:
        return full
    if un:
        return f"@{un}"
    return "Участник"


def _format_invited_line(row: Dict[str, Any]) -> str:
    name = html.escape(_invite_display_name(row))
    has_paid = bool(row.get("has_paid"))
    bonus_done = bool(row.get("referral_bonus_unlocked") or row.get("bonus_granted"))
    if has_paid:
        tail = (
            "есть оплата подписки; бонус +7 дней начислен"
            if bonus_done
            else "есть оплата; бонус скоро будет зафиксирован в системе"
        )
    else:
        tail = "пока без оплаты подписки"
    rd = row.get("referral_date")
    dt = ""
    if isinstance(rd, datetime):
        dt = rd.strftime("%d.%m.%Y")
    elif rd:
        dt = str(rd)[:10]
    if dt:
        return f"• {name} ({html.escape(dt)}): {html.escape(tail)}"
    return f"• {name}: {html.escape(tail)}"


class ReferralFeature(BaseFeature):
    """Фича реферальной программы"""

    @property
    def name(self) -> str:
        return "referral"

    async def initialize(self) -> None:
        """Инициализация фичи."""
        logger.info(f"[{self.name}] Фича инициализирована")

    def __init__(self, user_storage, bot):
        super().__init__()
        self.user_storage = user_storage
        self.aiogram_bot = bot

    def register_handlers(self, dp: Dispatcher) -> None:
        """Регистрирует обработчики фичи."""
        # Команда /affiliate обрабатывается в commands.py

    async def show_affiliate_link(self, message: Message, user_id: int) -> None:
        """Реферальная ссылка и краткая статистика успехов пользователя."""
        try:
            bot_username = await resolve_telegram_bot_username(self.aiogram_bot)
            if not bot_username:
                await message.answer(
                    "❌ Не удалось узнать адрес бота для ссылки. "
                    "Попробуйте позже или задайте в .env переменную <code>TELEGRAM_BOT_USERNAME</code> "
                    "(username без символа @).",
                    parse_mode=ParseMode.HTML,
                )
                return

            referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
            stats = await self.user_storage.get_referral_stats(user_id)
            invites = stats.get("total", 0)
            monthly = stats.get("monthly", 0)
            paid = stats.get("paid", 0)
            bonuses = stats.get("bonuses_given", 0)

            lines = [
                "<b>🤝 Поделись ссылкой с друзьями</b>\n\n"
                f'<a href="{html.escape(referral_link, quote=True)}">{html.escape(referral_link)}</a>\n\n'
                "<b>📊 Ваши результаты</b>\n"
                f"• Переходов по вашей ссылке (всего): <b>{invites}</b>\n"
                f"• Новых за последние 30 дней: <b>{monthly}</b>\n"
                f"• Оформили подписку (успешная оплата): <b>{paid}</b>\n"
                f"• Раз начислен бонус +7 дней вам: <b>{bonuses}</b>\n\n"
                "<i>📌 Как это работает:</i>\n"
                "• По вашей ссылке друзья попадут в бота\n"
                "• После первой оплаты подписки вашим рефералом "
                "вы получите +7 дней к лицензии бесплатно!\n\n",
            ]

            recent_rows = await self.user_storage.get_referrals_list(user_id, limit=5)
            if recent_rows:
                lines.append("<b>Последние приглашённые</b> (до 5):\n")
                lines.extend(_format_invited_line(r) + "\n" for r in recent_rows)
                lines.append("\n")
            elif invites == 0:
                lines.append(
                    "<i>Пока никто не зашёл по вашей ссылке — отправьте её близким из блока выше.</i>\n\n"
                )

            lines.append("Спасибо, что делитесь проектом! 🙏")

            await message.answer(
                "".join(lines),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info(f"✅ Affiliate link shown to user_id={user_id}")

        except Exception as e:
            logger.error(f"❌ Error showing affiliate link for user_id={user_id}: {e}", exc_info=True)
            await message.answer(
                "❌ Произошла ошибка при формировании реферальной ссылки. Попробуйте позже."
            )

    async def register_referral(self, message: Message, referrer_id_str: str, is_new_user: bool) -> None:
        """
        Регистрирует реферала, только если пользователь новый.

        Args:
            message: сообщение от пользователя
            referrer_id_str: ID реферера в виде строки
            is_new_user: флаг, был ли пользователь в БД до этого
        """
        try:
            user_id = message.from_user.id
            referrer_id = int(referrer_id_str)

            if not is_new_user:
                logger.info(f"User {user_id} already existed, skipping referral")
                return

            if referrer_id == user_id:
                logger.info(f"Self-referral attempt by {user_id}")
                return

            referrer = await self.user_storage.get_user(referrer_id)
            if not referrer:
                logger.warning(f"Referrer {referrer_id} not found")
                return

            created = await self.user_storage.process_referral(user_id, referrer_id)
            if created:
                logger.info(f"✅ Referral registered: {user_id} -> {referrer_id}")
                if user_id != 367302291:
                    await self._notify_referrer(referrer_id, message.from_user.first_name)
            else:
                logger.info(f"Referral not created (already linked or conflict): {user_id}")

        except ValueError as e:
            logger.error(f"❌ Invalid referrer_id: {referrer_id_str}, error: {e}")
        except Exception as e:
            logger.error(f"❌ Error registering referral: {e}", exc_info=True)

    async def _notify_referrer(self, referrer_id: int, user_name: str) -> None:
        """Отправляет уведомление рефереру о новом реферале."""
        try:
            user_display = user_name or "Новый пользователь"

            message_text = (
                f"✨ По вашей ссылке в клуб пришёл новый человек.\n\n"
                f"<b>{html.escape(user_display)}</b> теперь с нами.\n"
                f"Спасибо, что делитесь этим пространством с теми, кому тоже хочется быть ближе к Богу.\n\n"
                f"🎁 Когда ваш друг впервые оплатит подписку, вам добавится <b>+7 дней лицензии в подарок.</b> "
                f"Пусть это будет маленькой благодарностью за ваш труд любви.\n\n"
                f"<blockquote>Ибо не неправеден Бог, чтобы забыл дело ваше и труд любви, который вы оказали во имя Его…\nЕвреям 6:10</blockquote>"
            )

            await self.aiogram_bot.send_message(
                referrer_id,
                message_text,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"✅ Referral notification sent to {referrer_id}")

        except Exception as e:
            logger.error(f"❌ Failed to send referral notification to {referrer_id}: {e}")
