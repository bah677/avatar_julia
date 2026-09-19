"""
Общий каркас приложения Telegram-бота: пул апдейтов, middleware, очередь сообщений, жизненный цикл.

Конкретная «сборка» (клуб, Библия и т.д.) подключает фичи в подклассах через
`_register_features` и `_register_handlers`.
"""

import asyncio
import logging
from asyncio import Lock, Queue
from typing import Dict, Optional, Set

from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from bot.access.policies import LicenseWhitelistPolicy
from bot.features.base import FeatureManager
from bot.features.license_service import LicenseService
from bot.handlers.messages import route_message_to_feature
from bot.middleware.access_control import AccessControlMiddleware
from bot.middleware.group_chat_hygiene import GroupChatHygieneMiddleware
from bot.middleware.inbound_logging import InboundLoggingMiddleware
from bot.middleware.outgoing_logging import OutgoingLoggingMiddleware
from bot.payments.payment_checker import PaymentChecker
from bot.payments.yookassa_service import YooKassaService
from config import config
from openai_client.assistant import OpenAIClient
from rag.runtime import RagStack
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)


class TelegramBotApp:
    """Базовый класс процесса бота — без набора фич (их задаёт подкласс / клиент)."""

    async def send_chat_action(self, chat_id: int, action: str):
        await self.bot.send_chat_action(chat_id, action)

    def __init__(
        self,
        *,
        bot_token: Optional[str] = None,
        database_url: Optional[str] = None,
    ):
        self.bot = Bot(token=bot_token or config.MIRON_BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())

        self.user_storage = UserStorage(database_url or config.database_url)
        self.openai_client: Optional[OpenAIClient] = None
        self.license_service = LicenseService(self.user_storage)

        self.yookassa_service = YooKassaService()
        self.bzb_service = None
        self.payment_checker: Optional[PaymentChecker] = None

        # RAG (пакет ``rag/``); поднимается в ``initialize()`` если ``RAG_ENABLED`` и есть API-ключ.
        self.rag_stack: Optional[RagStack] = None

        self.feature_manager = FeatureManager()

        self.user_queues: Dict[int, Queue] = {}
        self.user_locks: Dict[int, Lock] = {}
        self.processing_users: Set[int] = set()
        self._queues_guard: Lock = Lock()
        self._worker_tasks: Set[asyncio.Task] = set()

    def _register_all_components(self) -> None:
        self._register_features()
        self._register_handlers()

    def _register_features(self) -> None:
        raise NotImplementedError("Задайте фичи в clients/<product>/core.py")

    def _register_handlers(self) -> None:
        raise NotImplementedError("Задайте хендлеры в clients/<product>/core.py")

    def _get_user_queue(self, user_id: int) -> Queue:
        queue = self.user_queues.get(user_id)
        if queue is None:
            queue = Queue()
            self.user_queues[user_id] = queue
            self.user_locks[user_id] = Lock()
        return queue

    def _get_user_lock(self, user_id: int) -> Lock:
        lock = self.user_locks.get(user_id)
        if lock is None:
            self.user_queues.setdefault(user_id, Queue())
            lock = Lock()
            self.user_locks[user_id] = lock
        return lock

    def _spawn_worker(self, user_id: int) -> None:
        task = asyncio.create_task(self._process_user_messages(user_id))
        self._worker_tasks.add(task)
        task.add_done_callback(self._worker_tasks.discard)

    async def add_to_queue(self, user_id: int, message_data: dict) -> None:
        async with self._queues_guard:
            queue = self._get_user_queue(user_id)
            await queue.put(message_data)
            need_spawn = user_id not in self.processing_users
            if need_spawn:
                self.processing_users.add(user_id)

        if need_spawn:
            self._spawn_worker(user_id)

    async def _process_user_messages(self, user_id: int) -> None:
        queue = self.user_queues.get(user_id)
        if queue is None:
            queue = Queue()
            self.user_queues[user_id] = queue
            self.user_locks.setdefault(user_id, Lock())

        try:
            while True:
                try:
                    message_data = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                message = message_data["message"]
                processed = message_data["processed"]
                message_id = message_data["message_id"]

                logger.debug("📨 Processing queued message for user_id=%s", user_id)

                key = StorageKey(
                    bot_id=self.bot.id,
                    chat_id=message.chat.id,
                    user_id=user_id,
                    thread_id=None,
                    business_connection_id=None,
                    destiny="default",
                )
                state = FSMContext(storage=self.dp.storage, key=key)

                current_state = await state.get_state()
                logger.info("🔄 Recovered state for user=%s: %s", user_id, current_state)

                try:
                    await route_message_to_feature(
                        message=message,
                        state=state,
                        processed=processed,
                        message_id=message_id,
                        feature_manager=self.feature_manager,
                    )
                except Exception as e:
                    logger.error(
                        "❌ Error routing message for user_id=%s: %s", user_id, e
                    )
                finally:
                    queue.task_done()

        finally:
            need_respawn = False
            async with self._queues_guard:
                self.processing_users.discard(user_id)
                live_queue = self.user_queues.get(user_id)
                if live_queue is not None and not live_queue.empty():
                    need_respawn = True
                    self.processing_users.add(user_id)
                elif user_id in self.user_queues:
                    self.user_queues.pop(user_id, None)
                    self.user_locks.pop(user_id, None)

            if need_respawn:
                self._spawn_worker(user_id)

    async def _start_background_tasks(self) -> None:
        try:
            if self.payment_feature is not None and config.PAYMENT_CHECKER_ENABLED:
                self.payment_checker = PaymentChecker(
                    user_storage=self.user_storage,
                    yookassa_service=self.yookassa_service,
                    bzb_service=self.bzb_service,
                    bot=self.bot,
                    currency_converter=self.currency_converter,
                    order_fulfillment=getattr(self, "order_fulfillment", None),
                    payment_feature=self.payment_feature,
                    feature_manager=self.feature_manager,
                )
                self.payment_checker.check_interval = 60
                await self.payment_checker.start()
                logger.info("✅ PaymentChecker запущен")
            else:
                self.payment_checker = None
                if self.payment_feature is None:
                    logger.info(
                        "PaymentChecker не запускается (нет фичи payment)"
                    )
                else:
                    logger.info(
                        "PaymentChecker отключён (PAYMENT_CHECKER_ENABLED не задан или false)"
                    )

            cg = self.feature_manager.get_optional("club_group")
            if cg is not None:
                await cg.start_background_tasks()

            yd = self.feature_manager.get_optional("yandex_disk_sync")
            if yd is not None:
                await yd.start_background_tasks()

            tm = self.feature_manager.get_optional("telemost_mail")
            if tm is not None:
                await tm.start_background_tasks()

            for fname in ("course_disk_sync", "content_studio"):
                feat = self.feature_manager.get_optional(fname)
                if feat is not None and hasattr(feat, "start_background_tasks"):
                    await feat.start_background_tasks()

            if getattr(config, "COURSE_ENABLED", False):
                from course.worker import CourseWorker

                self.course_worker = CourseWorker(self)
                self.course_worker.start()
                logger.info("course worker started")

            await self._set_bot_commands()

        except Exception as e:
            logger.error(f"❌ Ошибка запуска фоновых задач: {e}")

    async def _stop_background_tasks(self) -> None:
        try:
            cg = self.feature_manager.get_optional("club_group")
            if cg is not None:
                await cg.stop_background_tasks()

            yd = self.feature_manager.get_optional("yandex_disk_sync")
            if yd is not None:
                await yd.stop_background_tasks()

            tm = self.feature_manager.get_optional("telemost_mail")
            if tm is not None:
                await tm.stop_background_tasks()

            for fname in ("course_disk_sync", "content_studio"):
                feat = self.feature_manager.get_optional(fname)
                if feat is not None and hasattr(feat, "stop_background_tasks"):
                    await feat.stop_background_tasks()

            worker = getattr(self, "course_worker", None)
            if worker is not None:
                await worker.stop()

            if self.payment_checker:
                await self.payment_checker.stop()
                logger.info("✅ PaymentChecker остановлен")

        except Exception as e:
            logger.error(f"❌ Ошибка остановки фоновых задач: {e}")

    async def initialize(self) -> None:
        try:
            await self.user_storage.initialize()

            from bot.integrations.rag_bridge import try_build_rag_stack

            self.rag_stack = try_build_rag_stack(config)

            self.openai_client = OpenAIClient(self.user_storage)

            from bot.media_processing import MediaProcessor

            self.media_processor = MediaProcessor(
                user_storage=self.user_storage,
                openai_client=self.openai_client,
                bot=self.bot,
            )

            if config.BZB_API_KEY:
                from bot.payments.bzb_service import BZBService

                self.bzb_service = BZBService()
            else:
                self.bzb_service = None
                logger.warning("⚠️ BZB_API_KEY not set, BZB payments disabled")

            from bot.payments.currency_converter import CurrencyConverterService

            self.currency_converter = CurrencyConverterService()
            logger.info("✅ CurrencyConverterService initialized")

            from bot.logging.message_copier import MessageCopier
            from bot.logging.interaction_logger import InteractionLogger

            self.message_copier = MessageCopier(self.user_storage)
            self.interaction_logger = InteractionLogger(self.user_storage)

            if config.CLUB_GROUP_ID:
                self.dp.update.middleware(
                    GroupChatHygieneMiddleware(
                        bot=self.bot,
                        club_group_id=config.CLUB_GROUP_ID,
                        welcome_topic_id=config.WELCOME_TOPIC_ID,
                    )
                )
            self.dp.update.middleware(
                InboundLoggingMiddleware(
                    message_copier=self.message_copier,
                    interaction_logger=self.interaction_logger,
                )
            )
            self.dp.update.middleware(
                AccessControlMiddleware(
                    policy=LicenseWhitelistPolicy(
                        self.user_storage,
                        super_admin_id=config.SUPER_ADMIN_ID,
                        admin_only_mode=config.BOT_ACCESS_ADMIN_ONLY,
                    ),
                )
            )

            self.bot.session.middleware(
                OutgoingLoggingMiddleware(self.message_copier)
            )

            self._register_all_components()

            for feature in self.feature_manager.get_all().values():
                await feature.initialize()

            try:
                self.payment_feature = self.feature_manager.get_optional("payment")
            except Exception:
                self.payment_feature = None

            await self._start_background_tasks()

            logger.info("✅ Все зависимости бота инициализированы")

        except Exception as e:
            logger.error(f"❌ Ошибка инициализации зависимостей бота: {e}")
            raise

    async def _set_bot_commands(self) -> None:
        from aiogram.types import BotCommand

        cmds = [
            BotCommand(command="menu", description="Меню"),
        ]
        try:
            await self.bot.set_my_commands(cmds)
        except Exception as e:
            logger.warning("set_my_commands: %s", e)

    async def start(self) -> None:
        logger.info("🚀 Запуск бота...")

        try:
            await self.bot.delete_webhook(drop_pending_updates=True)
            logger.info("✅ Webhook сброшен")

            await asyncio.sleep(2)

            allowed_updates = [
                "message",
                "channel_post",
                "callback_query",
                "my_chat_member",
                "chat_member",
            ]

            await self.dp.start_polling(
                self.bot,
                allowed_updates=allowed_updates,
                skip_updates=True,
                timeout=60,
            )

        except Exception as e:
            logger.error(f"❌ Ошибка запуска бота: {e}")
            raise

    async def close(self) -> None:
        try:
            await self._stop_background_tasks()

            if self.openai_client and hasattr(self.openai_client, "close"):
                await self.openai_client.close()

            for _fname in (
                "support",
                "benefit",
                "club_group",
                "messaging",
                "referral",
                "payment",
                "media_id_helper",
            ):
                f = self.feature_manager.get_optional(_fname)
                if f is not None and hasattr(f, "teardown"):
                    try:
                        await f.teardown()
                    except Exception:
                        pass

            await self.user_storage.close()

            logger.info("✅ Ресурсы бота закрыты")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии ресурсов бота: {e}")
