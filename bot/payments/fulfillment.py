"""
Выдача продукта после успешной оплаты: единый вход для PaymentChecker и редких ручных проверок статуса.

Идемпотентность: повторный вызов с тем же успешным платежом не продлевает подписку дважды и не создаёт второй подарок по одному order_id.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import aiohttp

from aiogram.enums import ParseMode

from config import config, russian_days_phrase

from bot.payments.currency_converter import resolve_payment_datetime_for_rates

logger = logging.getLogger(__name__)


class PaidOrderFulfillment:
    def __init__(
        self,
        user_storage,
        bot,
        feature_manager,
        currency_converter,
    ):
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self.currency_converter = currency_converter

    async def compute_rub_amount(self, order: Dict[str, Any]) -> Optional[float]:
        return await self.currency_converter.convert_payment_amount(
            amount=float(order["amount"]),
            currency=order["currency"],
            payment_date=datetime.now(),
        )

    async def finalize_pending_payment_or_none(
        self,
        payment_id: int,
        provider_payment_id: str,
        rub_amount: float,
        exchange_rate: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Атомарно фиксирует оплату (pending→succeeded, заказ→paid). None если уже обработан.
        """
        return await self.user_storage.try_finalize_pending_payment_success(
            payment_id=payment_id,
            provider_payment_id=provider_payment_id,
            rub_amount=rub_amount,
            exchange_rate=exchange_rate,
        )

    async def deliver_after_successful_payment_row(
        self,
        payment: Dict[str, Any],
    ) -> None:
        """
        Вызывать только для строки payments со status=succeeded (после finalize или при доотгрузке).
        """
        await self._deliver_core(payment)

    async def ensure_delivered_for_payment_id(self, payment_id: int) -> bool:
        """
        Идемпотентно: если платёж уже succeeded, но продукт не выдан (сбой между шагами) — доотгрузить.
        """
        p = await self.user_storage.get_payment(payment_id)
        if not p or p.get("status") != "succeeded":
            return False
        await self._deliver_core(p)
        return True

    async def _deliver_core(self, payment: Dict[str, Any]) -> None:
        user_id = payment["user_id"]
        oid = payment.get("order_id")
        if oid is None:
            logger.error("❌ fulfillment: payment %s has no order_id", payment.get("id"))
            return

        order = await self.user_storage.get_order(oid)
        if not order:
            logger.error("❌ fulfillment: order %s not found", oid)
            return

        tariff = await self.user_storage.get_tariff_by_id(order["tariff_id"])
        if not tariff:
            logger.error("❌ fulfillment: tariff %s not found", order["tariff_id"])
            return

        duration_days = tariff["duration_days"]
        tariff_name = tariff["name"]
        order["tariff_name"] = tariff_name
        order["duration_days"] = duration_days
        order.setdefault("tariff_type", tariff.get("type"))

        rub_amount = await self.compute_rub_amount(order)
        if rub_amount is None:
            logger.error("❌ fulfillment: rub conversion failed order=%s", oid)
            return

        if order.get("is_gift"):
            await self._deliver_gift_order(
                payment=payment, order=order, duration_days=duration_days,
                rub_amount=rub_amount,
            )
            return

        if await self.user_storage.subscription_delivery_audit_exists(int(payment["id"])):
            logger.info(
                "ℹ️ Subscription already delivered for payment_id=%s, skip body",
                payment["id"],
            )
            await self._maybe_referral_only_if_missing(order, payment)
            return

        await self._deliver_subscription_order(
            payment=payment,
            order=order,
            duration_days=duration_days,
            rub_amount=rub_amount,
        )

    async def _deliver_gift_order(
        self,
        *,
        payment: Dict[str, Any],
        order: Dict[str, Any],
        duration_days: int,
        rub_amount: float,
    ) -> None:
        if await self.user_storage.get_gift_by_order_id(order["id"]):
            logger.info("ℹ️ Gift already exists for order_id=%s, skip recreate", order["id"])
            return

        logger.info(f"🎁 Processing gift order {order['id']} for user {order['user_id']}")
        gift_code = secrets.token_hex(8).upper()
        gift_days = config.GIFT_LINK_VALIDITY_DAYS
        gift_days_ru = russian_days_phrase(gift_days)
        expires_at = datetime.now() + timedelta(days=gift_days)

        gift_created = await self.user_storage.create_gift(
            order_id=order["id"],
            user_id=order["user_id"],
            tariff_id=order["tariff_id"],
            gift_code=gift_code,
            expires_at=expires_at,
        )

        if gift_created:
            bot_info = await self.bot.get_me()
            bot_username = bot_info.username
            gift_link = f"https://t.me/{bot_username}?start=gift_{gift_code}"

            donor_message = (
                f"<b>🎁 Подарок успешно оплачен!</b>\n\n"
                f"Вы подарили подписку в клуб «Разговоры с Богом» на {duration_days} дней.\n\n"
                f"<b>📌 Как это работает:</b>\n"
                f"1️⃣ Отправьте сообщение, которое мы подготовили для вас ниже — тому, кому хотите подарить подписку.\n"
                f"2️⃣ Получатель перейдет по ссылке и активирует подарок.\n"
                f"3️⃣ После активации у него откроется доступ в закрытый клуб.\n\n"
                f"<b>🔐 Важно:</b>\n"
                f"• Ссылка <b>одноразовая</b> — после активации станет недействительной.\n"
                f"• Срок активации — <b>{gift_days_ru}</b>. Если получатель не активирует подарок в этот срок, ссылка сгорит.\n"
                f"• Сама подписка начнет действовать <b>с момента активации</b> получателем.\n\n"
                f"Спасибо за вашу щедрость! 🙏"
            )
            try:
                await self.bot.send_message(
                    order["user_id"],
                    donor_message,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.error(f"❌ Failed to send gift instruction to donor: {e}")

            share_message = (
                f"✨ <b>Вам подарок!</b>\n\n"
                f"Я дарю вам подписку в клуб «Разговоры с Богом» на {duration_days} дней.\n\n"
                f"🎁 <b>Как активировать:</b>\n"
                f"Перейдите по ссылке — подарок активируется, и вы получите доступ в закрытый клуб.\n\n"
                f"🔗 <b>Ссылка для активации:</b>\n"
                f"{gift_link}\n\n"
                f"⏰ <b>Важно:</b> ссылка одноразовая и активна {gift_days_ru}. Подписка начнет действовать с момента перехода.\n\n"
                f"Пусть этот подарок станет для вас шагом к живому общению 🙏"
            )
            try:
                await self.bot.send_message(
                    order["user_id"],
                    share_message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"❌ Failed to send gift share message to donor: {e}")

            await self._notify_admins_about_gift(order, expires_at, rub_amount)
        else:
            logger.error(f"❌ Failed to create gift record for order {order['id']}")

    async def _deliver_subscription_order(
        self,
        *,
        payment: Dict[str, Any],
        order: Dict[str, Any],
        duration_days: int,
        rub_amount: float,
    ) -> None:
        buyer_id = order["user_id"]
        current_license = await self.user_storage.get_user_active_license(buyer_id)
        now = datetime.now()

        was_license_active = bool(
            current_license and current_license["expires_at"] > now
        )
        if was_license_active and current_license:
            base_date = current_license["expires_at"]
            logger.info(f"📅 Extending existing license from {base_date}")
        else:
            base_date = now
            logger.info(f"📅 New license from {base_date}")

        new_expiry = base_date + timedelta(days=duration_days)

        await self.user_storage.create_or_extend_license(
            user_id=buyer_id,
            order_id=order["id"],
            expires_at=new_expiry,
            audit_source="subscription_payment",
            audit_payment_id=int(payment["id"]),
            audit_order_id=int(order["id"]),
        )

        updated_license = await self.user_storage.get_user_active_license(buyer_id)
        await self._send_user_notification(order, rub_amount, updated_license)
        await self._notify_admins_about_donation(
            order, payment, rub_amount, updated_license, was_license_active
        )

        if not was_license_active:
            club_group = self.feature_manager.get_optional("club_group")
            if club_group:
                await club_group.send_group_invite(buyer_id)
            else:
                logger.warning("⚠️ ClubGroupFeature not found, invite not sent")
        else:
            logger.info(
                "ℹ️ User %s already had active license, skipping invite",
                buyer_id,
            )

        await self._apply_referral_bonus_first_base_payment(order, payment)

    async def _maybe_referral_only_if_missing(
        self,
        order: Dict[str, Any],
        payment: Dict[str, Any],
    ) -> None:
        """
        Если продукт уже выдан по audit, но реф-бонус не выдали (редкий крайний случай).
        Условия те же что и при полной выдаче.
        """
        await self._apply_referral_bonus_first_base_payment(order, payment)

    async def _apply_referral_bonus_first_base_payment(
        self,
        order: Dict[str, Any],
        payment: Dict[str, Any],
    ) -> None:
        buyer_id = order["user_id"]
        referrer_info = await self.user_storage.get_referrer_info(buyer_id)
        if not referrer_info or referrer_info.get("bonus_granted"):
            return

        ttype = (order.get("tariff_type") or "").strip()
        if ttype != "base":
            logger.info(
                "ℹ️ Referral bonus skipped: tariff_type=%r (not base) buyer=%s",
                ttype,
                buyer_id,
            )
            return

        base_count = await self.user_storage.count_successful_base_tariff_payments(buyer_id)
        if base_count != 1:
            logger.info(
                "ℹ️ Referral bonus skipped: base tariff payment count=%s buyer=%s",
                base_count,
                buyer_id,
            )
            return

        referrer_id = referrer_info["referrer_id"]
        success = await self.user_storage.extend_license_by_days(
            referrer_id, 7, audit_referred_user_id=buyer_id
        )
        if not success:
            logger.error(f"❌ Failed to extend license for referrer {referrer_id}")
            return

        await self.user_storage.mark_referral_bonus_granted(buyer_id)
        referrer_license = await self.user_storage.get_user_active_license(referrer_id)
        expires_str = (
            referrer_license["expires_at"].strftime("%d.%m.%Y")
            if referrer_license
            else "неизвестно"
        )
        try:
            await self.bot.send_message(
                referrer_id,
                f"<b>🎉 Подарок за друга!</b>\n\n"
                f"По вашей ссылке присоединился пользователь и оформил подписку (базовый тариф).\n"
                f"✨ Ваша лицензия продлена на 7 дней бесплатно!\n"
                f"📅 <b>Новая дата окончания:</b> {expires_str}\n\n"
                f"Спасибо, что делитесь проектом! 🙏",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            msg = str(e).lower()
            if any(
                x in msg
                for x in ("bot was blocked", "user is deactivated", "chat not found")
            ):
                logger.warning(f"⚠️ Cannot notify referrer {referrer_id}")
                await self.user_storage.deactivate_user(referrer_id)
            else:
                logger.error(f"❌ Failed referral bonus DM to {referrer_id}: {e}")

    async def _send_user_notification(
        self, order: Dict[str, Any], rub_amount: float, license_row: Dict[str, Any]
    ):
        try:
            user_id = order["user_id"]
            expires_str = (
                license_row["expires_at"].strftime("%d.%m.%Y")
                if license_row and license_row.get("expires_at")
                else "неизвестно"
            )
            message = (
                f"✅ <b>Оплата прошла успешно!</b>\n\n"
                f"🎉 Подписка <b>{order['tariff_name']}</b> активирована.\n"
                f"📅 <b>Дата окончания:</b> {expires_str}\n"
                f"💰 Сумма: {order['amount']} {order['currency']}\n\n"
                f"📅 Чтобы узнать срок подписки в любой момент: команда /subs\n\n"
                f"Спасибо за вашу поддержку! ❤️"
            )
            await self.bot.send_message(user_id, message, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"❌ Failed to send user notification: {e}")

    async def _notify_admins_about_donation(
        self,
        order: Dict[str, Any],
        payment: Dict[str, Any],
        rub_amount: float,
        lic: Dict[str, Any],
        is_renewal: bool = False,
    ):
        try:
            admin_bot_token = config.telegram_token_for_admin_channel
            admin_channel_id = config.ADMIN_CHANNEL_ID
            admin_thread_id = config.PAYMENT_THREAD_ID
            if not admin_bot_token or not admin_channel_id:
                logger.warning("⚠️ Admin bot or channel not configured")
                return

            user_data = payment.get("user_telegram_data", {})
            if isinstance(user_data, str):
                user_data = json.loads(user_data)
            full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
            full_name = full_name or "Не указано"
            username_display = (
                "@" + user_data["username"] if user_data.get("username") else "нет username"
            )

            import html as html_mod

            expires_str = (
                lic["expires_at"].strftime("%d.%m.%Y") if lic and lic.get("expires_at") else "N/A"
            )

            title = "💰 ПРОДЛЕНИЕ ЛИЦЕНЗИИ" if is_renewal else "💰 НОВЫЙ ПЛАТЕЖ"
            source_name = await self.user_storage.get_last_referral_source(order["user_id"])
            assistant_msgs_count = await self.user_storage.get_assistant_messages_count(
                order["user_id"]
            )
            notification_text = (
                f"{title}\n\n"
                f"📋 <b>Тариф:</b> {html_mod.escape(order['tariff_name'])}\n"
                f"💰 <b>Сумма:</b> {order['amount']} {order['currency']}\n"
                f"💳 <b>В рублях:</b> {rub_amount:.2f} RUB\n"
                f"👤 <b>Пользователь:</b> {html_mod.escape(full_name)}\n"
                f"🆔 <b>User ID:</b> <code>{order['user_id']}</code>\n"
                f"📱 <b>Username:</b> {html_mod.escape(username_display)}\n"
                f"🔗 <b>Источник:</b> {html_mod.escape(source_name or 'неизвестно')}\n"
                f"💬 <b>Вопросов Боту:</b> {html_mod.escape(str(assistant_msgs_count or '0'))}\n"
                f"📅 <b>Лицензия до:</b> {expires_str}"
            )

            async with aiohttp.ClientSession() as session:
                post_data: Dict[str, Any] = {
                    "chat_id": admin_channel_id,
                    "text": notification_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                if admin_thread_id and admin_thread_id > 0:
                    post_data["message_thread_id"] = admin_thread_id
                async with session.post(
                    f"https://api.telegram.org/bot{admin_bot_token}/sendMessage",
                    json=post_data,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"❌ Failed to notify admin: {await resp.text()}")
        except Exception as e:
            logger.error(f"❌ Error notifying admins: {e}")

    async def _notify_admins_about_gift(
        self, order: Dict[str, Any], gift_expires_at: datetime, rub_amount: float
    ):
        try:
            admin_bot_token = config.telegram_token_for_admin_channel
            admin_channel_id = config.ADMIN_CHANNEL_ID
            admin_thread_id = config.PAYMENT_THREAD_ID
            if not admin_bot_token or not admin_channel_id:
                return

            import html as html_mod

            user = await self.user_storage.get_user(order["user_id"])
            user_data = user or {}
            full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip() or "Не указано"
            username_display = (
                "@" + user_data["username"] if user_data.get("username") else "нет username"
            )
            expires_str = gift_expires_at.strftime("%d.%m.%Y")
            notification_text = (
                f"💰 <b>НОВЫЙ ПЛАТЕЖ</b>\n\n"
                f"📋 <b>Тариф:</b> {html_mod.escape(order['tariff_name'])}\n"
                f"💰 <b>Сумма:</b> {order['amount']} {order['currency']}\n"
                f"💳 <b>В рублях:</b> {rub_amount:.2f} RUB\n"
                f"👤 <b>Пользователь:</b> {html_mod.escape(full_name)}\n"
                f"🆔 <b>User ID:</b> <code>{order['user_id']}</code>\n"
                f"📱 <b>Username:</b> {html_mod.escape(username_display)}\n"
                f"🎁 <b>Лицензия в подарок</b>\n"
                f"⏰ <b>Срок активации:</b> до {expires_str}"
            )

            async with aiohttp.ClientSession() as session:
                post_data: Dict[str, Any] = {
                    "chat_id": admin_channel_id,
                    "text": notification_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                if admin_thread_id and admin_thread_id > 0:
                    post_data["message_thread_id"] = admin_thread_id
                async with session.post(
                    f"https://api.telegram.org/bot{admin_bot_token}/sendMessage",
                    json=post_data,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"❌ Failed to notify admin about gift: {await resp.text()}")
        except Exception as e:
            logger.error(f"❌ Error notifying admins about gift: {e}")
