# bot/features/base.py
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any

from config import config

logger = logging.getLogger(__name__)


class BaseFeature(ABC):
    """Базовый класс для всех фич бота."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Имя фичи."""
        pass
    
    def __init__(self):
        self.config = config
    
    async def initialize(self) -> None:
        """Инициализация фичи."""
        logger.info(f"[{self.name}] Фича инициализирована")
    
    @abstractmethod
    def register_handlers(self, dispatcher) -> None:
        """Регистрация обработчиков для фичи."""
        pass
    
    def log(self, message: str, level: str = "info") -> None:
        """Логирование с префиксом фичи."""
        log_method = getattr(logger, level.lower(), logger.info)
        log_method(f"[{self.name}] {message}")


class FeatureManager:
    """Менеджер для управления фичами."""
    
    def __init__(self):
        self.features: Dict[str, BaseFeature] = {}
    
    def register(self, feature: BaseFeature) -> None:
        """Регистрация фичи."""
        self.features[feature.name] = feature
        feature.log(f"Фича зарегистрирована")
    
    def get(self, name: str) -> BaseFeature:
        """Получение фичи по имени."""
        if name not in self.features:
            raise KeyError(f"Фича '{name}' не найдена")
        return self.features[name]

    def get_optional(self, name: str):
        """Фича по имени или None (для опциональных модулей клуба в монорепо и т.п.)."""
        return self.features.get(name)
    
    def get_all(self) -> Dict[str, BaseFeature]:
        """Получение всех фич."""
        return self.features.copy()
    
    async def initialize_all(self) -> None:
        """Инициализация всех фич."""
        for feature in self.features.values():
            try:
                await feature.initialize()
                feature.log("Инициализирована")
            except Exception as e:
                feature.log(f"Ошибка инициализации: {e}", "error")