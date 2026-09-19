# bot/payments/payment_checker.py
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from bot.payments.standalone_donation_notify import (
    notify_admins_standalone_donation_success,
)

logger = logging.getLogger(__name__)


class PaymentChecker:
    """Сервис для периодической проверки статуса платежей"""

    def __init__(
        self,
        user_storage,
        yookassa_service,
        bzb_service,
        bot,
        currency_converter,
        order_fulfillment=None,
        payment_feature=None,
        feature_manager=None,
        check_interval: int = 300,
    ):
        self.user_storage = user_storage
        self.yookassa_service = yookassa_service
        self.bzb_service = bzb_service
        self.bot = bot
        self.currency_converter = currency_converter
        self.order_fulfillment = order_fulfillment
        self.payment_feature = payment_feature
        self.feature_manager = feature_manager
        self.check_interval = check_interval
        self.is_running = False
        self.check_task: asyncio.Task = None

    async def start(self):
        """Запускает периодическую проверку платежей"""
        self.is_running = True
        self.check_task = asyncio.create_task(self._check_payments_loop())
        logger.info("✅ Payment checker started")

    async def stop(self):
        """Останавливает проверку платежей"""
        self.is_running = False
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        logger.info("✅ Payment checker stopped")

    async def _check_payments_loop(self):
        """Основной цикл проверки платежей"""
        while self.is_running:
            try:
                await self._check_pending_payments()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in payment check loop: {e}")
                await asyncio.sleep(30)

    async def _check_pending_payments(self):
        """Проверяет все ожидающие платежи по всем провайдерам"""
        try:
            three_days_ago = datetime.now() - timedelta(days=3)
            all_pending = await self.user_storage.get_pending_payments(three_days_ago)

            if not all_pending:
                return

            yookassa_payments = [p for p in all_pending if p.get("payment_provider") == "yookassa"]
            bzb_payments = [p for p in all_pending if p.get("payment_provider") == "bzb"]

            for payment in yookassa_payments:
                try:
                    await self._check_single_payment(payment, "yookassa")
                except Exception as e:
                    logger.error(f"❌ Failed to check YooKassa payment {payment['id']}: {e}")

            for payment in bzb_payments:
                try:
                    await self._check_single_payment(payment, "bzb")
                except Exception as e:
                    logger.error(f"❌ Failed to check BZB payment {payment['id']}: {e}")

        except Exception as e:
            logger.error(f"❌ Error checking pending payments: {e}")

    async def _check_single_payment(self, payment: Dict[str, Any], provider: str):
        """Проверяет статус одного платежа для конкретного провайдера"""
        payment_id = payment["id"]
        provider_payment_id = payment["provider_payment_id"]
        user_id = payment["user_id"]

        if not provider_payment_id:
            logger.warning(f"⚠️ No provider_payment_id for payment {payment_id}")
            return

        try:
            if provider == "yookassa":
                service = self.yookassa_service
            elif provider == "bzb":
                if not self.bzb_service:
                    logger.warning(f"⚠️ BZB service not available for payment {payment_id}")
                    return
                service = self.bzb_service
            else:
                logger.error(f"❌ Unknown provider: {provider}")
                return

            status, details = await service.check_payment_status(provider_payment_id)

            if status == "succeeded":
                logger.info(f"💰 Payment {payment_id} ({provider}) succeeded")

                if payment.get("order_id") is None:
                    await self._finalize_standalone_payment(
                        payment_id=payment_id,
                        user_id=user_id,
                        provider_payment_id=provider_payment_id,
                    )
                    return

                if not self.order_fulfillment:
                    logger.error(
                        "❌ order_fulfillment не задан, пропуск оплаты с заказом id=%s",
                        payment_id,
                    )
                    return

                order = await self.user_storage.get_order(payment["order_id"])
                if not order:
                    logger.error(f"❌ Order {payment['order_id']} not found for payment {payment_id}")
                    return

                rub_amount = await self.order_fulfillment.compute_rub_amount(
                    order, payment
                )
                if not rub_amount:
                    logger.error(f"❌ Failed to convert payment {payment_id} to RUB")
                    return

                exchange_rate = rub_amount / float(order["amount"])

                finalized = await self.order_fulfillment.finalize_pending_payment_or_none(
                    payment_id=payment_id,
                    provider_payment_id=provider_payment_id,
                    rub_amount=rub_amount,
                    exchange_rate=exchange_rate,
                )

                fresh = await self.user_storage.get_payment(payment_id)
                if not fresh or fresh.get("status") != "succeeded":
                    logger.error(f"❌ Payment {payment_id} not succeeded after finalize attempt")
                    return

                await self.order_fulfillment.deliver_after_successful_payment_row(fresh)
                logger.debug(
                    "finalize outcome payment_id=%s claimed_row=%s",
                    payment_id,
                    finalized is not None,
                )

            elif status in ["canceled", "failed"]:
                logger.info(f"❌ Payment {payment_id} is {status}")
                await self.user_storage.update_payment_status(
                    payment_id=payment_id,
                    status=status,
                    provider_payment_id=provider_payment_id,
                )

        except Exception as e:
            logger.error(f"❌ Error processing {provider} payment {payment_id}: {e}", exc_info=True)

    async def _finalize_standalone_payment(
        self,
        *,
        payment_id: int,
        user_id: int,
        provider_payment_id: str,
    ) -> None:
        """Донаты и прочие платежи без заказа (order_id IS NULL)."""
        try:
            await self.user_storage.update_payment_status(
                payment_id=payment_id,
                status="succeeded",
                provider_payment_id=provider_payment_id,
            )
            payment_row = await self.user_storage.get_payment(payment_id)
            try:
                if payment_row:
                    rub_amount = None
                    conv = getattr(self, "currency_converter", None)
                    if conv is not None:
                        pay_dt_raw = payment_row.get("completed_at") or payment_row.get(
                            "updated_at"
                        )
                        pay_dt = pay_dt_raw or datetime.now()
                        if hasattr(pay_dt, "year"):
                            dt = pay_dt
                        else:
                            dt = datetime.now()
                        try:
                            rub_amount = await conv.convert_payment_amount(
                                float(payment_row.get("amount") or 0),
                                (payment_row.get("currency") or "RUB").strip(),
                                dt,
                            )
                        except Exception as fx_e:
                            logger.warning(
                                "FX для админ-уведомления donation payment_id=%s: %s",
                                payment_id,
                                fx_e,
                            )
                    if rub_amount is None and payment_row.get("amount_rub") is not None:
                        rub_amount = float(payment_row["amount_rub"])
                    await notify_admins_standalone_donation_success(
                        self.user_storage,
                        payment_row,
                        rub_amount=rub_amount,
                    )
            except Exception as adm_e:
                logger.error(
                    "❌ Админ-уведомление о standalone-платеже id=%s: %s",
                    payment_id,
                    adm_e,
                    exc_info=True,
                )

            try:
                await self.bot.send_message(
                    user_id,
                    "🙏 <b>Спасибо за поддержку проекта!</b>\n\n"
                    "Ваше пожертвование получено — пусть оно вернётся к вам сторицей.",
                    parse_mode="HTML",
                )
            except Exception as send_e:
                logger.warning(
                    "Не удалось отправить благодарность user=%s: %s", user_id, send_e
                )
        except Exception as e:
            logger.error(
                "❌ Ошибка финализации standalone payment_id=%s: %s",
                payment_id,
                e,
                exc_info=True,
            )
