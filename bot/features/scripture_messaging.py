"""Диалог с агентом: DeepSeek + история + RAG (см. MessagingFeature)."""

import logging

from aiogram import Dispatcher

from bot.features.messaging import MessagingFeature
from openai_client.agents_client import AgentsClient

logger = logging.getLogger(__name__)


class ScriptureMessagingFeature(MessagingFeature):
    """То же, что MessagingFeature; имя файла историческое."""

    def __init__(self, user_storage, message_copier, feature_manager):
        super().__init__(user_storage, message_copier, feature_manager)

    def register_handlers(self, dp: Dispatcher) -> None:
        """Точки входа — общие MessageHandlers."""
        super().register_handlers(dp)

    async def initialize(self) -> None:
        logger.info("[%s] Фича инициализируется", self.name)
        self.agents_client = AgentsClient(self.user_storage)
        from bot.features.creative_rag_coordinator import CreativeRagCoordinator

        self.creative_coord = CreativeRagCoordinator(self)
        logger.info("[%s] ✅ AgentsClient (DeepSeek) готов", self.name)
