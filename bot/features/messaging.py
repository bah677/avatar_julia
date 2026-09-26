# bot/features/messaging.py
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from bot.features.base import BaseFeature
from bot.features.golden_chroma_fields import (
    GOLDEN_FLOW_PRIVATE_DM,
    GoldenSnapshot,
    build_golden_extra_metadata_async,
)
from bot.utils.telegram_chunk import split_telegram_html_chunks
from bot.utils.telegram_html import strip_subscribe_cta
from bot.utils.telegram_html_async import normalize_llm_reply_for_telegram_async

logger = logging.getLogger(__name__)


@dataclass
class GoldenPendingVote:
    """Данные для сохранения в golden_examples по нажатию 👍 под первым чанком ответа."""

    topic: str
    answer: str
    snapshot: GoldenSnapshot


class MessagingFeature(BaseFeature):
    """Личка с ботом: диалог + RAG (материалы из группы), золотой фонд по 👍."""
    
    name = "messaging"
    
    def __init__(self, user_storage, message_copier, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.message_copier = message_copier
        self.feature_manager = feature_manager
        self.bot = None
        self.agents_client = None
        self.creative_coord = None  # CreativeRagCoordinator — сценарий /new + RAG-задача
        # 👍 под первым сообщением с ответом: метаданные снимаем с сценария (/new vs обычная личка).
        self._golden_vote_by_msg: Dict[Tuple[int, int], GoldenPendingVote] = {}

    def set_bot(self, bot):
        """Устанавливает экземпляр бота."""
        self.bot = bot
    
    async def initialize(self) -> None:
        """Инициализация фичи."""
        logger.info(f"[{self.name}] Фича инициализирована")
        
        from openai_client.agents_client import AgentsClient
        from bot.features.creative_rag_coordinator import CreativeRagCoordinator

        self.agents_client = AgentsClient(self.user_storage)
        self.creative_coord = CreativeRagCoordinator(self)
        logger.info(f"[{self.name}] ✅ Agents client initialized")

    async def teardown(self) -> None:
        """Очистка при отключении фичи."""
        logger.info(f"[{self.name}] Фича остановлена")
    
    def register_handlers(self, dp: Dispatcher) -> None:
        """Регистрирует обработчики."""
        pass

    async def handle_chat_message(self, message: Message, state: FSMContext, 
                                   text: str, message_id: int = None):
        """Обработчик всех сообщений. Маршрутизация лички — ТЗ §6.5."""
        user_id = message.from_user.id
        
        logger.info(f"📨 Получено сообщение от {user_id}: {text[:50]}...")

        await self.user_storage.save_user_from_message(message)

        from bot.states import (
            DislikeReasonStates,
            MenuFocusStates,
            PassportTalkStates,
            StoriesUploadStates,
            StyleUploadStates,
        )
        from config import config as app_config

        cur = await state.get_state()
        studio = self.feature_manager.get_optional("content_studio")
        style_f = self.feature_manager.get_optional("style_feature")
        intake = self.feature_manager.get_optional("course_intake")
        menu = self.feature_manager.get_optional("main_menu")
        passports = self.feature_manager.get_optional("passport_wizard")
        stories_up = self.feature_manager.get_optional("stories_intake")

        if cur == DislikeReasonStates.waiting_text.state and studio:
            if await studio.try_handle_dislike_text(message, state, text):
                return
        if cur == StyleUploadStates.waiting_file.state and style_f:
            await style_f.on_upload(message, state)
            return
        if cur == MenuFocusStates.waiting_text.state and menu:
            if await menu.try_handle_focus_text(message, state, text):
                return
        if cur == PassportTalkStates.talking.state and passports:
            if await passports.try_handle_text(message, state, text):
                return
        if cur == StoriesUploadStates.collecting.state and stories_up:
            if await stories_up.try_handle_upload(message, state, text):
                return

        if studio and message.reply_to_message:
            if await studio.try_handle_draft_reply(message, text):
                return

        if intake and await intake.try_handle_links(message, text or message.caption or ""):
            return

        if getattr(app_config, "COURSE_ENABLED", False) and self.creative_coord:
            if await self.creative_coord.try_handle_private_message(message, text):
                return
            return

        if self.creative_coord and await self.creative_coord.try_handle_private_message(
            message, text
        ):
            return

        rag = getattr(self.bot, "rag_stack", None) if self.bot else None

        async def _dialog() -> None:
            agent_response = await self._get_agent_response(user_id, text, rag_stack=rag)
            if agent_response:
                await self._send_to_user(
                    message,
                    agent_response,
                    rag_vote=rag is not None,
                    golden_topic=text,
                    golden_snapshot=GoldenSnapshot(
                        source_flow=GOLDEN_FLOW_PRIVATE_DM,
                    ),
                )
            else:
                await message.reply(
                    "Что-то пошло не так. Попробуйте еще раз",
                )

        tg = self.bot.bot if self.bot else None
        if tg:
            async with ChatActionSender.typing(
                message.chat.id,
                tg,
                message.message_thread_id,
            ):
                await _dialog()
        else:
            await _dialog()
    
    async def _get_agent_response(
        self,
        user_id: int,
        question: str,
        *,
        rag_stack=None,
    ) -> Optional[str]:
        """Ответ DeepSeek: история ЛС + при наличии RAG — фрагменты базы и golden few-shot."""
        try:
            if not self.agents_client:
                logger.warning("Agents client not initialized")
                return None

            retrieved, golden_block = "", ""
            if rag_stack is not None:
                from config import config as app_config

                hist = await self.user_storage.get_private_chat_history(
                    user_id,
                    limit=app_config.RAG_RETRIEVAL_CONTEXT_USER_TURNS + 4,
                )
                user_turns = [
                    (m.get("content") or "").strip()
                    for m in hist
                    if m.get("role") == "user" and (m.get("content") or "").strip()
                ]
                q = (question or "").strip()
                if q and (not user_turns or user_turns[-1] != q):
                    user_turns.append(q)
                plan_ctx = user_turns[-app_config.RAG_RETRIEVAL_CONTEXT_USER_TURNS :]
                retrieved, golden_block = await asyncio.gather(
                    rag_stack.retriever.retrieve_for_avatar_task_async(
                        user_turns=plan_ctx,
                        context_user_turns=app_config.RAG_RETRIEVAL_CONTEXT_USER_TURNS,
                        testimonial_max_chunks=app_config.RAG_TESTIMONIAL_MAX_CHUNKS,
                    ),
                    rag_stack.golden.format_few_shot_block_async(
                        "\n".join(plan_ctx) or q,
                        top_k=2,
                    ),
                )

            response = await self.agents_client.run(
                user_message=question,
                user_id=user_id,
                retrieved_context=retrieved or "",
                golden_block=golden_block or "",
            )
            return response

        except Exception as e:
            logger.error(f"❌ Agent response failed for user {user_id}: {e}")
            return None

    async def send_agent_reply(
        self,
        message: Message,
        response: str,
        *,
        rag_vote: bool = False,
        golden_topic: Optional[str] = None,
        golden_snapshot: Optional[GoldenSnapshot] = None,
    ) -> None:
        """Публичная обёртка для ответов агента (в т.ч. сценарий /new)."""
        await self._send_to_user(
            message,
            response,
            rag_vote=rag_vote,
            golden_topic=golden_topic,
            golden_snapshot=golden_snapshot,
        )

    async def _send_to_user(
        self,
        message: Message,
        response: str,
        *,
        rag_vote: bool = False,
        golden_topic: Optional[str] = None,
        golden_snapshot: Optional[GoldenSnapshot] = None,
    ):
        """Отправляет ответ (HTML); при RAG — одна кнопка 👍 (золотой фонд)."""
        try:
            body, _wants_cta = strip_subscribe_cta(response)
            uid = message.from_user.id if message.from_user else 0
            oc = self.agents_client
            response_html = await normalize_llm_reply_for_telegram_async(
                body,
                user_id=uid,
                agents_client=oc,
            )

            vote_kb: Optional[InlineKeyboardMarkup] = None
            if rag_vote:
                vote_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="👍 В золотой фонд",
                                callback_data="rag_vote:up",
                            ),
                        ]
                    ]
                )

            tg = self.bot.bot if self.bot else None
            chunks = split_telegram_html_chunks(response_html)
            reply_msg: Optional[Message] = None
            for i, part in enumerate(chunks):
                if i == 0:
                    reply_msg = await message.reply(
                        part,
                        parse_mode=ParseMode.HTML,
                        reply_markup=vote_kb,
                    )
                elif tg:
                    await tg.send_message(
                        chat_id=message.chat.id,
                        text=part,
                        parse_mode=ParseMode.HTML,
                        message_thread_id=message.message_thread_id,
                    )
                else:
                    logger.error(
                        "❌ Bot unset: dropped %s follow-up chunk(s) for user",
                        len(chunks) - 1,
                    )
                    break
            if rag_vote and reply_msg is not None:
                key = (reply_msg.chat.id, reply_msg.message_id)
                t = (golden_topic or "").strip() or "(запрос без текста)"
                snap = golden_snapshot or GoldenSnapshot(
                    source_flow=GOLDEN_FLOW_PRIVATE_DM,
                )
                self._golden_vote_by_msg[key] = GoldenPendingVote(
                    topic=t,
                    answer=body.strip(),
                    snapshot=snap,
                )
            logger.info(
                "✅ Agent response sent to user %s (%s part(s))",
                message.from_user.id,
                len(chunks),
            )
        except Exception as e:
            logger.error(f"❌ Failed to send response to user: {e}")

    async def on_rag_vote_feedback(self, callback: CallbackQuery) -> None:
        """👍 под ответом бота — сохранить пару в золотой фонд."""
        uid = callback.from_user.id if callback.from_user else 0
        msg = callback.message
        if not msg:
            await callback.answer("Нет сообщения.", show_alert=True)
            return

        key = (msg.chat.id, msg.message_id)
        pending = self._golden_vote_by_msg.pop(key, None)
        if not pending:
            await callback.answer(
                "Этот ответ уже оценён или устарел. Попросите бота ещё раз.",
                show_alert=True,
            )
            return

        topic, answer, snap = pending.topic, pending.answer, pending.snapshot

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        rag = getattr(self.bot, "rag_stack", None) if self.bot else None
        if not rag:
            await callback.answer("База примеров недоступна", show_alert=True)
            return

        try:
            extra = await build_golden_extra_metadata_async(
                topic=topic,
                answer=answer,
                added_by=uid,
                snapshot=snap,
            )
        except Exception as e:
            logger.exception("golden metadata build failed: %s", e)
            extra = {}

        rid = await rag.golden.add_example_async(
            topic, answer, extra_metadata=extra or None
        )
        if rid:
            try:
                await self.user_storage.log_interaction(
                    user_id=uid,
                    event_category="rag",
                    event_type="golden_example_user_vote",
                    data={
                        "topic_chars": len(topic),
                        "answer_chars": len(answer),
                        "golden_flow": snap.source_flow,
                        "has_product": bool((snap.product or "").strip()),
                    },
                    source="callback",
                    outcome="success",
                )
            except Exception as e:
                logger.debug("golden vote log_interaction: %s", e)
            await callback.answer("Добавили в золотой фонд — текст сообщения можно скопировать 👍")
        else:
            await callback.answer("Не удалось сохранить пример", show_alert=True)
