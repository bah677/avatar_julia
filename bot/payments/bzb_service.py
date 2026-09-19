"""
Сервис для работы с BZB Payment API.
"""

import logging
import uuid
import json
from typing import Optional, Tuple, Dict, Any
from datetime import datetime

import aiohttp
from config import config

from .base import PaymentProvider

logger = logging.getLogger(__name__)


class BZBService(PaymentProvider):
    """Сервис для работы с BZB Payment API"""
    
    def __init__(self):
        self.api_key = config.BZB_API_KEY
        self.base_url = config.BZB_API_URL or "https://pay.bzbtests.online"
        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }
        logger.info("✅ BZB service initialized")
    
    @property
    def provider_name(self) -> str:
        return "bzb"
    
    async def create_payment(
        self,
        amount: float,
        description: str,
        user_id: int,
        payment_type: str = "one_time",
        bot_username: str = None,
        **kwargs
    ) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        """
        Создает платеж в BZB.
        
        Args:
            amount: сумма в рублях
            description: описание платежа
            user_id: ID пользователя
            payment_type: тип платежа
            bot_username: username бота для back_url
            **kwargs: дополнительные параметры (title, back_url и т.д.)
            
        Returns:
            Tuple[payment_url, payment_id, metadata]
        """
        try:
            # Формируем URL для возврата
            back_url = f"https://t.me/{bot_username}" if bot_username else None
            
            # Заголовок платежа (краткое описание)
            title = kwargs.get('title', description[:50])
            
            # Данные для запроса
            payload = {
                "amount": float(amount),
                "currency": "USD",
                "title": title,
                "description": description[:500],
                "back_url": back_url
            }
            
            logger.info(f"💰 BZB create payment: {payload}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/v1/payments",
                    headers=self.headers,
                    json=payload
                ) as response:
                    
                    if response.status == 201:
                        data = await response.json()
                        
                        payment_id = data.get('id')
                        payment_url = data.get('payment_url')
                        short_id = data.get('short_id')
                        
                        # Метаданные для сохранения
                        metadata = {
                            'short_id': short_id,
                            'created_at': data.get('created_at'),
                            'raw_response': data
                        }
                        
                        logger.info(f"✅ BZB payment created: {payment_id}, url: {payment_url}")
                        return payment_url, payment_id, metadata
                        
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ BZB create payment failed: {response.status} - {error_text}")
                        return None, None, None
                        
        except Exception as e:
            logger.error(f"❌ BZB create payment error: {e}", exc_info=True)
            return None, None, None
    
    async def check_payment_status(self, payment_id: str) -> Tuple[str, Dict[str, Any]]:
        """
        Проверяет статус платежа в BZB.
        
        Returns:
            Tuple[status, details]
            status: 'pending', 'confirmed', 'cancelled'
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/v1/payments/{payment_id}",
                    headers=self.headers
                ) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        # Маппинг статусов BZB в наши статусы
                        status_map = {
                            'pending': 'pending',
                            'confirmed': 'succeeded',
                            'cancelled': 'canceled'
                        }
                        
                        bzb_status = data.get('status', 'pending')
                        our_status = status_map.get(bzb_status, 'pending')
                        
                        details = {
                            'id': data.get('id'),
                            'status': our_status,
                            'amount': data.get('amount'),
                            'currency': data.get('currency'),
                            'raw_status': bzb_status,
                            'raw_response': data
                        }
                        
                        #logger.info(f"✅ BZB payment {payment_id} status: {bzb_status} -> {our_status}")
                        return our_status, details
                        
                    else:
                        logger.error(f"❌ BZB check status failed: {response.status}")
                        return 'error', {}
                        
        except Exception as e:
            logger.error(f"❌ BZB check status error: {e}")
            return 'error', {}