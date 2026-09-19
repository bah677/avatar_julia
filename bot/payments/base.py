"""
Базовый класс для всех платежных сервисов.
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple, Dict, Any


class PaymentProvider(ABC):
    """Базовый класс для провайдеров платежей"""
    
    @abstractmethod
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
        Создает платеж.
        
        Returns:
            Tuple[payment_url, payment_id, metadata]
        """
        pass
    
    @abstractmethod
    async def check_payment_status(self, payment_id: str) -> Tuple[str, Dict[str, Any]]:
        """
        Проверяет статус платежа.
        
        Returns:
            Tuple[status, details]
        """
        pass
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Возвращает имя провайдера"""
        pass