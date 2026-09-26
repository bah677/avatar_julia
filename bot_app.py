"""Сборка Telegram-бота: фичи и регистрация хендлеров."""

import logging
from typing import Optional

from bot.base_app import TelegramBotApp
from bot.features.scripture_messaging import ScriptureMessagingFeature
from bot.handlers.messages import MessageHandlers

from command_handlers import AppCommandHandlers
from config import BibliaBotConfig, load_biblia_bot_config

logger = logging.getLogger(__name__)


class BotApplication(TelegramBotApp):
    """Контент-аватар эксперта: диск, банк идей, черновики."""

    def __init__(self, biblia_cfg: Optional[BibliaBotConfig] = None):
        bc = biblia_cfg or load_biblia_bot_config()
        super().__init__(
            bot_token=bc.BIBLIA_BOT_TOKEN,
            database_url=bc.database_url,
        )
        self.course_worker = None

    def _register_features(self) -> None:
        from bot.features.content_studio import ContentStudioFeature
        from bot.features.course_disk_sync import CourseDiskSyncFeature
        from bot.features.course_intake import CourseIntakeFeature
        from bot.features.group_rag_indexer import GroupRagIndexerFeature
        from bot.features.main_menu import MainMenuFeature
        from bot.features.passport_wizard import PassportWizardFeature
        from bot.features.stories_intake import StoriesIntakeFeature
        from bot.features.style_feature import StyleFeature

        messaging_feature = ScriptureMessagingFeature(
            user_storage=self.user_storage,
            message_copier=self.message_copier,
            feature_manager=self.feature_manager,
        )
        main_menu = MainMenuFeature()
        course_disk_sync = CourseDiskSyncFeature()
        course_intake = CourseIntakeFeature()
        content_studio = ContentStudioFeature()
        style_feature = StyleFeature()
        passport_wizard = PassportWizardFeature()
        stories_intake = StoriesIntakeFeature()
        group_rag = GroupRagIndexerFeature()

        features = [
            messaging_feature,
            main_menu,
            course_disk_sync,
            course_intake,
            content_studio,
            style_feature,
            passport_wizard,
            stories_intake,
            group_rag,
        ]
        for feature in features:
            self.feature_manager.register(feature)
            if hasattr(feature, "set_bot"):
                feature.set_bot(self)
            feature.register_handlers(self.dp)

        logger.info("✅ Зарегистрировано фич: %s", len(features))

    def _register_handlers(self) -> None:
        AppCommandHandlers(self.dp, self.feature_manager).register_handlers()

        payment = self.feature_manager.get_optional("payment")
        if payment is not None:
            payment.register_handlers(self.dp)

        message_handlers = MessageHandlers(
            self.dp,
            self.feature_manager,
            self.media_processor,
            self.message_copier,
            self.interaction_logger,
        )
        message_handlers.set_bot(self)
        message_handlers.register_handlers()
