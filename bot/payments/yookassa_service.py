import asyncio
import logging
import uuid
import aiohttp
import json
from typing import Dict, Optional, Tuple
from yookassa import Configuration, Payment
from config import config

logger = logging.getLogger(__name__)


# ВАЖНО: yookassa SDK синхронный (requests). Любой его вызов в event loop'е блокирует
# весь asyncio (включая aiogram polling) до сетевого таймаута. По умолчанию у
# requests НЕТ таймаута — поэтому одно зависшее соединение к api.yookassa.ru
# может «повесить» бот на часы. Поэтому:
#   1) вызовы выполняем строго в отдельном потоке через asyncio.to_thread,
#   2) ограничиваем суммарное время ожидания через asyncio.wait_for.
_YOOKASSA_CALL_TIMEOUT_SEC = 20


class YooKassaService:
    """Сервис для работы с прямым API ЮKassa."""

    def __init__(self):
        """Инициализация клиента ЮKassa."""
        try:
            Configuration.account_id = config.YOOKASSA_SHOP_ID
            Configuration.secret_key = config.YOOKASSA_SECRET_KEY
            logger.info("✅ YooKassa service initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize YooKassa: {e}")
            raise

    @staticmethod
    async def _run_blocking(func, *args, **kwargs):
        """Выполняет синхронный вызов SDK в отдельном потоке с таймаутом."""
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args, **kwargs),
            timeout=_YOOKASSA_CALL_TIMEOUT_SEC,
        )

    async def create_payment(
        self,
        amount: float,
        description: str,
        user_id: int,
        payment_type: str = "one_time",
        bot_username: str = None,
        save_payment_method: bool = False  # 🔥 НОВЫЙ ПАРАМЕТР
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Создает платеж в ЮKassa и возвращает (confirmation_url, payment_id, payment_method_id).
        
        Args:
            amount: сумма в рублях
            description: описание платежа
            user_id: ID пользователя в Telegram
            payment_type: тип платежа (one_time или subscription)
            bot_username: username бота для return_url
            save_payment_method: сохранить способ оплаты для подписок
            
        Returns:
            Tuple[confirmation_url, payment_id, payment_method_id]
        """
        try:
            # 🔥 СОЗДАЕМ ГЛУБОКУЮ ССЫЛКУ ДЛЯ ВОЗВРАТА
            return_url = f"https://t.me/{bot_username}"
            
            # Формируем метаданные
            metadata = {
                "telegram_user_id": str(user_id),
                "payment_type": payment_type,
                "bot_platform": "telegram"
            }
            
            # 🔥 Формируем данные для чека (54-ФЗ)
            receipt_data = {
                "customer": {
                    "email": "user@example.com",
                },
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": {
                            "value": f"{amount:.2f}",
                            "currency": "RUB"
                        },
                        "vat_code": 1,
                        "payment_mode": "full_payment",
                        "payment_subject": "commodity"
                    }
                ],
                "tax_system_code": 1
            }
            
            # 🔥 ПАРАМЕТРЫ ДЛЯ ПОДПИСКИ
            payment_data = {
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url
                },
                "capture": True,
                "description": description,
                "metadata": metadata,
                "receipt": receipt_data
            }
            
            # 🔥 ЕСЛИ ЭТО ПОДПИСКА - СОХРАНЯЕМ СПОСОБ ОПЛАТЫ
            #if payment_type == "subscription" or save_payment_method:
            #    payment_data["save_payment_method"] = True
            
            # Создаем платеж (synchronous SDK -> вызываем в thread с таймаутом)
            payment = await self._run_blocking(
                Payment.create,
                payment_data,
                str(uuid.uuid4()),  # Идемпотентный ключ
            )
            
            # Получаем данные для ответа
            payment_id = payment.id
            confirmation_url = payment.confirmation.confirmation_url
            
            # 🔥 ИНИЦИАЛИЗИРУЕМ ПЕРЕМЕННЫЕ
            payment_method_id = None
            card_last4 = None
            card_type = None
            pm = None
            
            if hasattr(payment, 'payment_method') and payment.payment_method:
                pm = payment.payment_method
                # Сохраняем информацию о карте
                if hasattr(pm, 'id'):
                    payment_method_id = pm.id
                if hasattr(pm, 'card') and hasattr(pm.card, 'last4'):
                    card_last4 = pm.card.last4
                if hasattr(pm, 'card') and hasattr(pm.card, 'card_type'):
                    card_type = pm.card.card_type
                
                # Если нет ID, создаем свой на основе last4
                if not payment_method_id and card_last4:
                    from datetime import datetime
                    payment_method_id = f"pm_{card_last4}_{int(datetime.now().timestamp())}"
            
            # 🔥 СОЗДАЕМ METADATA С ДАННЫМИ КАРТЫ
            metadata = {
                'card_last4': card_last4,
                'payment_method_id': payment_method_id,
                'card_saved': save_payment_method,
                'card_type': card_type
            }
            
            logger.info(
                "💰 Payment created: %s card_last4=%s payment_method_id=%s meta=%s",
                payment_id,
                card_last4,
                payment_method_id,
                metadata,
            )

            # Ровно 3 значения — см. вызывающий код (donation_payment и тип возврата).
            return confirmation_url, payment_id, payment_method_id

        except asyncio.TimeoutError:
            logger.error(
                f"⏱ YooKassa create_payment timeout (>{_YOOKASSA_CALL_TIMEOUT_SEC}s) "
                f"for user {user_id}"
            )
            return None, None, None
        except Exception as e:
            logger.error(f"❌ Failed to create payment: {e}", exc_info=True)
            return None, None, None

    async def create_subscription_payment(
        self,
        amount: float,
        description: str,
        payment_method_id: str,
        user_id: int
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Создает платеж по подписке с использованием сохраненного способа оплаты.
        
        Args:
            amount: сумма в рублях
            description: описание платежа
            payment_method_id: ID сохраненного способа оплаты
            user_id: ID пользователя
            
        Returns:
            Tuple[payment_id, status]
        """
        try:
            import uuid
            
            metadata = {
                "telegram_user_id": str(user_id),
                "payment_type": "subscription_recurring",
                "bot_platform": "telegram"
            }
            
            # Данные для чека
            receipt_data = {
                "customer": {
                    "email": "user@example.com",
                },
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": {
                            "value": f"{amount:.2f}",
                            "currency": "RUB"
                        },
                        "vat_code": 1,
                        "payment_mode": "full_payment",
                        "payment_subject": "commodity"
                    }
                ],
                "tax_system_code": 1
            }
            
            # Создаем платеж с сохраненным способом оплаты (synchronous SDK -> в thread)
            payment = await self._run_blocking(
                Payment.create,
                {
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": "RUB",
                    },
                    "payment_method_id": payment_method_id,
                    "capture": True,
                    "description": description,
                    "metadata": metadata,
                    "receipt": receipt_data,
                },
                str(uuid.uuid4()),
            )
            
            payment_id = payment.id
            status = payment.status

            logger.info(f"💰 Subscription payment created: {payment_id}, "
                    f"status: {status}, amount: {amount} RUB")

            return payment_id, status

        except asyncio.TimeoutError:
            logger.error(
                f"⏱ YooKassa create_subscription_payment timeout "
                f"(>{_YOOKASSA_CALL_TIMEOUT_SEC}s) for user {user_id}"
            )
            return None, None
        except Exception as e:
            logger.error(f"❌ Failed to create subscription payment: {e}", exc_info=True)
            return None, None

    async def check_payment_status(self, payment_id: str) -> Tuple[str, dict]:
        """Проверяет статус платежа в ЮKassa"""
        try:
            payment = await self._run_blocking(Payment.find_one, payment_id)
            status = payment.status
            
            details = {
                'id': payment.id,
                'status': status,
                'paid': getattr(payment, 'paid', False),
                'amount': getattr(getattr(payment, 'amount', None), 'value', '0'),
                'currency': getattr(getattr(payment, 'amount', None), 'currency', 'RUB'),
            }
            
            # 🔥 ПРАВИЛЬНО: Используем payment_method.id как payment_method_id
            payment_method = getattr(payment, 'payment_method', None)
            
            if payment_method:
                # Это и есть идентификатор сохраненного способа оплаты!
                payment_method_id = getattr(payment_method, 'id', None)
                
                if payment_method_id:
                    details['payment_method_id'] = payment_method_id
                    #logger.info(f"✅ Using payment_method.id as payment_method_id: {payment_method_id}")
                
                # Проверяем, сохранена ли карта
                is_saved = getattr(payment_method, 'saved', False)
                details['card_saved'] = is_saved
                #logger.info(f"💰 Card saved status: {is_saved}")
                
                # Сохраняем данные карты для отображения пользователю
                card_info = getattr(payment_method, 'card', None)
                if card_info:
                    details['card_last4'] = getattr(card_info, 'last4', None)
                    details['card_type'] = getattr(card_info, 'card_type', None)
                    #logger.info(f"💰 Card: ****{details.get('card_last4')} ({details.get('card_type')})")
            
            #logger.info(f"✅ Payment {payment_id} details: {details}")
            return status, details

        except asyncio.TimeoutError:
            logger.error(
                f"⏱ YooKassa check_payment_status timeout (>{_YOOKASSA_CALL_TIMEOUT_SEC}s) "
                f"for {payment_id}; treating as still pending"
            )
            return 'pending', {}
        except Exception as e:
            logger.error(f"❌ Error checking payment status {payment_id}: {e}", exc_info=True)
            return 'error', {}