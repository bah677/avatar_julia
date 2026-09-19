"""Уведомление админ-канала о успешном донате без заказа (order_id IS NULL)."""

from __future__ import annotations

import html as html_module
import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from config import config

logger = logging.getLogger(__name__)


def _telegram_block_from_payment(payment: Dict[str, Any]) -> tuple[str, str, str]:
    user_data = payment.get("user_telegram_data") or {}
    if isinstance(user_data, str):
        try:
            user_data = json.loads(user_data)
        except json.JSONDecodeError:
            user_data = {}
    if not isinstance(user_data, dict):
        user_data = {}
    full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
    full_name = full_name or "Не указано"
    username_display = (
        "@" + user_data["username"] if user_data.get("username") else "нет username"
    )
    return full_name, username_display, str(payment.get("user_id") or "")


def _fmt_rub_line(rub_amount: Optional[float]) -> str:
    if rub_amount is None:
        return "💵 <b>В рублях:</b> —"
    v = float(rub_amount)
    if abs(v - round(v)) < 0.005:
        rub_s = str(int(round(v)))
    else:
        rub_s = f"{v:.2f}"
    return f"💵 <b>В рублях:</b> {html_module.escape(rub_s)} RUB"


async def notify_admins_standalone_donation_success(
    user_storage,
    payment: Dict[str, Any],
    *,
    rub_amount: Optional[float] = None,
) -> None:
    admin_bot_token = config.telegram_token_for_admin_channel
    admin_channel_id = config.ADMIN_CHANNEL_ID
    admin_thread_id = getattr(config, "PAYMENT_THREAD_ID", None) or 0
    if not admin_bot_token or not admin_channel_id:
        logger.warning(
            "⚠️ BIBLIA_BOT_TOKEN or ADMIN_CHANNEL_ID not configured; skip standalone donation notify"
        )
        return

    user_id = int(payment["user_id"])
    full_name, username_display, _uid_str = _telegram_block_from_payment(payment)

    amt = payment.get("amount")
    currency = (payment.get("currency") or "").strip().upper() or ""

    resolved_rub = rub_amount
    if resolved_rub is None and payment.get("amount_rub") is not None:
        resolved_rub = float(payment["amount_rub"])
    if resolved_rub is None and currency == "RUB" and amt is not None:
        resolved_rub = float(amt)

    source_name: Optional[str] = None
    try:
        source_name = await user_storage.get_last_referral_source(user_id)
    except Exception as e:
        logger.debug("referral source for notify: %s", e)

    assistant_n = await user_storage.get_assistant_messages_count(user_id)

    amount_bit = html_module.escape(str(amt)).strip()
    curr_bit = html_module.escape(currency)

    notification_text = (
        "💰 ДОНАТ\n\n"
        f"💰 <b>Сумма:</b> {amount_bit} {curr_bit}\n"
        f"{_fmt_rub_line(resolved_rub)}\n"
        f"👤 <b>Пользователь:</b> {html_module.escape(full_name)}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📱 <b>Username:</b> {html_module.escape(username_display)}\n"
        f"🔗 <b>Источник:</b> {html_module.escape(source_name or 'неизвестно')}\n"
        f"💬 <b>Ответов аватара в истории:</b> "
        f"{html_module.escape(str(assistant_n or '0'))}\n"
    )

    post_data = {
        "chat_id": admin_channel_id,
        "text": notification_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if admin_thread_id and admin_thread_id > 0:
        post_data["message_thread_id"] = admin_thread_id

    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{admin_bot_token}/sendMessage"
            async with session.post(url, json=post_data) as resp:
                if resp.status != 200:
                    logger.error(
                        "❌ Failed standalone donation admin notify: %s",
                        await resp.text(),
                    )
    except Exception as e:
        logger.error("❌ Error sending standalone donation admin notify: %s", e, exc_info=True)
