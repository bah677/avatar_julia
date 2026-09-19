"""
Разовые донаты Biblia: RUB (YooKassa), USD (BZB), крипта.
Имена колбэков и сценарий как в Biblia/app/features/payments.py.
Платёж пишется в ``payments`` без ``order_id``.
"""

import json
import logging
import os
from typing import Optional

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.filters.private_only import CALLBACK_PRIVATE_CHAT, PRIVATE_CHAT
from bot.features.base import BaseFeature
from bot.utils.telegram_identity import resolve_telegram_bot_username

logger = logging.getLogger(__name__)


class DonationPaymentStates(StatesGroup):
    waiting_custom_rub_amount = State()
    waiting_custom_usd_amount = State()


class DonationPaymentFeature(BaseFeature):
    """Совместимо с ``PaymentChecker``: ``name == \"payment\"``."""

    name = "payment"

    def __init__(self, user_storage, yookassa_service, bzb_service, bot):
        super().__init__()
        self.user_storage = user_storage
        self.yookassa_service = yookassa_service
        self.bzb_service = bzb_service
        self.bot = bot

    async def initialize(self) -> None:
        logger.info("[%s] Фича донатов инициализирована", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.callback_query.register(
            self.handle_callback,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith("payment_"),
        )
        dp.message.register(
            self.on_custom_amount_message,
            PRIVATE_CHAT,
            StateFilter(DonationPaymentStates.waiting_custom_rub_amount),
            F.text,
        )
        dp.message.register(
            self.on_custom_amount_message,
            PRIVATE_CHAT,
            StateFilter(DonationPaymentStates.waiting_custom_usd_amount),
            F.text,
        )

    def _currency_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🇷🇺 Рубли (карты РФ)",
                        callback_data="payment_currency_rub",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💵 Доллары (карты не РФ)",
                        callback_data="payment_currency_usd",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="₿ Криптовалюта",
                        callback_data="payment_crypto",
                    )
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
            ]
        )

    def _rub_amount_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="300 ₽", callback_data="payment_rub_amount_300")],
                [InlineKeyboardButton(text="500 ₽", callback_data="payment_rub_amount_500")],
                [InlineKeyboardButton(text="1000 ₽", callback_data="payment_rub_amount_1000")],
                [InlineKeyboardButton(text="✏️ Другая сумма", callback_data="payment_rub_custom")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_currency")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
            ]
        )

    def _usd_amount_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="3 $", callback_data="payment_usd_amount_3")],
                [InlineKeyboardButton(text="10 $", callback_data="payment_usd_amount_10")],
                [InlineKeyboardButton(text="15 $", callback_data="payment_usd_amount_15")],
                [InlineKeyboardButton(text="✏️ Другая сумма", callback_data="payment_usd_custom")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_currency")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
            ]
        )

    def _cancel_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_currency")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
            ]
        )

    async def _bump_donation_menu_open(self, user_id: int) -> None:
        try:
            async with self.user_storage.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                       SET donation_button_click = COALESCE(donation_button_click, 0) + 1
                     WHERE user_id = $1
                    """,
                    user_id,
                )
        except Exception as e:
            logger.debug("donation_button_click skip: %s", e)

    async def show_donation_menu(self, message: Message, *, from_user_id: Optional[int] = None) -> None:
        uid = from_user_id
        if uid is None and message.from_user:
            uid = message.from_user.id
        if uid is None:
            uid = 0
        await self._bump_donation_menu_open(uid)
        await message.answer(
            "🤝 **Поддержи развитие нашего проекта**\n\n"
            "Твоя поддержка поможет сделать его лучше для всех пользователей.\n"
            "Ты вкладываешь в Благое дело 🙏🏻\n\n"
            "Выберите валюту для доната:",
            reply_markup=self._currency_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def handle_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = callback.data or ""
        if data == "payment_start":
            await callback.answer()
            if callback.message is None:
                logger.warning("payment_start без message")
                return
            uid = callback.from_user.id if callback.from_user else 0
            await self.show_donation_menu(callback.message, from_user_id=uid)
            return
        if data == "payment_currency_rub":
            await self._show_rub_amounts(callback, state)
        elif data == "payment_currency_usd":
            await self._show_usd_amounts(callback, state)
        elif data == "payment_crypto":
            await self._handle_crypto_donation(callback)
        elif data.startswith("payment_rub_amount_"):
            amount = int(data.replace("payment_rub_amount_", ""))
            await self._create_rub_payment(callback, amount)
        elif data.startswith("payment_usd_amount_"):
            amount = int(data.replace("payment_usd_amount_", ""))
            await self._create_usd_payment(callback, amount)
        elif data == "payment_rub_custom":
            await self._ask_custom_amount(callback, state, "rub")
        elif data == "payment_usd_custom":
            await self._ask_custom_amount(callback, state, "usd")
        elif data == "payment_back_currency":
            await self._back_to_currency(callback, state)
        elif data == "payment_cancel":
            await self._cancel(callback, state)
        else:
            await callback.answer("❌ Неизвестная команда")

    async def _show_rub_amounts(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.edit_text(
            "💎 **Любая сумма будет ценна для проекта**",
            reply_markup=self._rub_amount_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _show_usd_amounts(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.edit_text(
            "💎 **Любая сумма будет ценна для проекта**",
            reply_markup=self._usd_amount_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _ask_custom_amount(
        self, callback: CallbackQuery, state: FSMContext, currency: str
    ) -> None:
        await state.update_data(currency=currency)
        if currency == "rub":
            await state.set_state(DonationPaymentStates.waiting_custom_rub_amount)
            text = "💎 **Введите сумму в рублях (минимум 100 ₽):**"
        else:
            await state.set_state(DonationPaymentStates.waiting_custom_usd_amount)
            text = "💎 **Введите сумму в долларах (минимум 1 $):**"
        await callback.message.edit_text(
            text,
            reply_markup=self._cancel_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def on_custom_amount_message(self, message: Message, state: FSMContext) -> None:
        try:
            data = await state.get_data()
            currency = data.get("currency")
            amount = int(message.text.strip())
            if currency == "rub" and amount < 100:
                await message.answer(
                    "❌ Минимальная сумма 100 рублей. Введите сумму 100 или больше."
                )
                return
            if currency == "usd" and amount < 1:
                await message.answer(
                    "❌ Минимальная сумма 1 доллар. Введите сумму 1 или больше."
                )
                return
            try:
                await message.delete()
            except Exception:
                pass
            if currency == "rub":
                await self._create_rub_payment_from_message(message, amount)
            else:
                await self._create_usd_payment_from_message(message, amount)
            await state.clear()
        except ValueError:
            await message.answer("❌ Пожалуйста, введите корректную сумму (только цифры)")
        except Exception as e:
            logger.error("❌ custom amount: %s", e, exc_info=True)
            await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")

    def _user_telegram_json(self, user) -> str:
        return json.dumps(
            {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "username": user.username,
                "language_code": user.language_code,
            },
            ensure_ascii=False,
        )

    async def _create_rub_payment(self, callback: CallbackQuery, amount: int) -> None:
        try:
            user = callback.from_user
            user_id = user.id
            await callback.answer("⏳ Создаю платеж...")
            bot_username = await resolve_telegram_bot_username(self.bot)
            if not bot_username:
                await callback.message.edit_text(
                    "❌ Не задан username бота. Укажите TELEGRAM_BOT_USERNAME в .env",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            confirmation_url, provider_pid, _pmid = await self.yookassa_service.create_payment(
                amount=float(amount),
                description=f"Донат {amount} RUB",
                user_id=user_id,
                payment_type="one_time",
                bot_username=bot_username,
                save_payment_method=False,
            )
            if not confirmation_url or not provider_pid:
                raise RuntimeError("empty yookassa response")
            row_id = await self.user_storage.create_payment(
                user_id=user_id,
                amount=float(amount),
                payment_type="one_time",
                provider="yookassa",
                provider_payment_id=provider_pid,
                user_telegram_data=self._user_telegram_json(user),
                currency="RUB",
                order_id=None,
                provider_checkout_url=confirmation_url,
            )
            if not row_id:
                raise RuntimeError("create_payment failed")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Оплатить", url=confirmation_url)],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
                ]
            )
            await callback.message.edit_text(
                f"💰 **Сумма:** {amount} RUB\n\n"
                f"Для оплаты перейдите по ссылке ниже:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            logger.info("💰 RUB donation payment row=%s yk=%s user=%s", row_id, provider_pid, user_id)
        except Exception as e:
            logger.error("❌ RUB payment: %s", e, exc_info=True)
            await callback.message.edit_text(
                "❌ Не удалось создать платеж. Попробуйте позже.",
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _create_rub_payment_from_message(self, message: Message, amount: int) -> None:
        try:
            user = message.from_user
            user_id = user.id
            bot_username = await resolve_telegram_bot_username(self.bot)
            if not bot_username:
                await message.answer(
                    "❌ Не задан username бота (TELEGRAM_BOT_USERNAME в .env)."
                )
                return
            confirmation_url, provider_pid, _pmid = await self.yookassa_service.create_payment(
                amount=float(amount),
                description=f"Донат {amount} RUB",
                user_id=user_id,
                payment_type="one_time",
                bot_username=bot_username,
                save_payment_method=False,
            )
            if not confirmation_url or not provider_pid:
                raise RuntimeError("empty yookassa response")
            row_id = await self.user_storage.create_payment(
                user_id=user_id,
                amount=float(amount),
                payment_type="one_time",
                provider="yookassa",
                provider_payment_id=provider_pid,
                user_telegram_data=self._user_telegram_json(user),
                currency="RUB",
                order_id=None,
                provider_checkout_url=confirmation_url,
            )
            if not row_id:
                raise RuntimeError("create_payment failed")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Оплатить", url=confirmation_url)],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
                ]
            )
            await message.answer(
                f"💰 **Сумма:** {amount} RUB\n\n"
                f"Для оплаты перейдите по ссылке ниже:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            logger.info("💰 RUB donation payment row=%s user=%s", row_id, user_id)
        except Exception as e:
            logger.error("❌ RUB payment from message: %s", e, exc_info=True)
            await message.answer("❌ Не удалось создать платеж. Попробуйте позже.")

    async def _create_usd_payment(self, callback: CallbackQuery, amount: int) -> None:
        if not self.bzb_service:
            await callback.answer("Оплата в $ недоступна", show_alert=True)
            return
        try:
            user = callback.from_user
            user_id = user.id
            await callback.answer("⏳ Создаю платеж...")
            bot_username = await resolve_telegram_bot_username(self.bot)
            if not bot_username:
                await callback.message.edit_text(
                    "❌ Не задан username бота (TELEGRAM_BOT_USERNAME).",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            confirmation_url, provider_pid, _meta = await self.bzb_service.create_payment(
                amount=float(amount),
                description=f"Donation {amount} USD",
                user_id=user_id,
                payment_type="one_time",
                bot_username=bot_username,
                currency="USD",
            )
            if not confirmation_url or not provider_pid:
                raise RuntimeError("empty BZB response")
            row_id = await self.user_storage.create_payment(
                user_id=user_id,
                amount=float(amount),
                payment_type="one_time",
                provider="bzb",
                provider_payment_id=provider_pid,
                user_telegram_data=self._user_telegram_json(user),
                currency="USD",
                order_id=None,
                provider_checkout_url=confirmation_url,
            )
            if not row_id:
                raise RuntimeError("create_payment failed")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Оплатить", url=confirmation_url)],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
                ]
            )
            await callback.message.edit_text(
                f"💰 **Сумма:** {amount} USD\n\n"
                f"Для оплаты перейдите по ссылке ниже:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            logger.info("💰 USD donation payment row=%s bzb=%s user=%s", row_id, provider_pid, user_id)
        except Exception as e:
            logger.error("❌ USD payment: %s", e, exc_info=True)
            await callback.message.edit_text(
                "❌ Не удалось создать платеж. Попробуйте позже.",
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _create_usd_payment_from_message(self, message: Message, amount: int) -> None:
        if not self.bzb_service:
            await message.answer("Оплата в долларах временно недоступна.")
            return
        try:
            user = message.from_user
            user_id = user.id
            bot_username = await resolve_telegram_bot_username(self.bot)
            if not bot_username:
                await message.answer("❌ Не задан username бота.")
                return
            confirmation_url, provider_pid, _meta = await self.bzb_service.create_payment(
                amount=float(amount),
                description=f"Donation {amount} USD",
                user_id=user_id,
                payment_type="one_time",
                bot_username=bot_username,
                currency="USD",
            )
            if not confirmation_url or not provider_pid:
                raise RuntimeError("empty BZB response")
            row_id = await self.user_storage.create_payment(
                user_id=user_id,
                amount=float(amount),
                payment_type="one_time",
                provider="bzb",
                provider_payment_id=provider_pid,
                user_telegram_data=self._user_telegram_json(user),
                currency="USD",
                order_id=None,
                provider_checkout_url=confirmation_url,
            )
            if not row_id:
                raise RuntimeError("create_payment failed")
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Оплатить", url=confirmation_url)],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
                ]
            )
            await message.answer(
                f"💰 **Сумма:** {amount} USD\n\n"
                f"Для оплаты перейдите по ссылке ниже:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            logger.info("💰 USD donation payment row=%s user=%s", row_id, user_id)
        except Exception as e:
            logger.error("❌ USD payment from msg: %s", e, exc_info=True)
            await message.answer("❌ Не удалось создать платеж. Попробуйте позже.")

    async def _handle_crypto_donation(self, callback: CallbackQuery) -> None:
        address = (
            os.getenv("BIBLIA_CRYPTO_USDT_TRON_ADDRESS", "").strip()
            or "TTq5YQ8NHowe9zT4bqW7gW79kDeioFCnpu"
        )
        msg = (
            "💎 **Донат криптовалютой**\n\n"
            "Вы можете поддержать проект, отправив средства на следующий адрес:\n\n"
            f"`{address}`\n\n"
            "📌 **Сеть:** TRC-20 (Tron)\n"
            "💡 **Важно:** Убедитесь, что используете правильную сеть для перевода.\n\n"
            "Спасибо за вашу поддержку! ❤️"
        )
        await callback.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN)
        await callback.answer("✅ Адрес для перевода")

    async def _back_to_currency(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.edit_text(
            "🤝 **Поддержи развитие нашего проекта**\n\n"
            "Твоя поддержка поможет сделать его лучше для всех пользователей.\n"
            "Ты вкладываешь в Благое дело 🙏🏻\n\n"
            "Выберите валюту для доната:",
            reply_markup=self._currency_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.delete()
        await callback.answer("❌ Операция отменена")
