"""
Сценарий /new: подтверждение при активной задаче → сразу диалог с аватаром.

Продукт и тип контента аватар определяет в процессе (блок AGENT_META в ответе).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, List, Optional

from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import config
from bot.features.golden_chroma_fields import (
    GOLDEN_FLOW_CREATIVE_TASK,
    GoldenSnapshot,
)
from bot.features.rag_group_metadata import infer_content_category
from openai_client.agent_meta import split_agent_meta
from openai_client.task_rag_prompt import (
    build_dialogue_system_prompt,
    build_opening_system_prompt,
)
from storage.db.creative_sessions import (
    CS_ACTIVE,
    CS_CONFIRM_NEW,
    CS_IDLE,
)

if TYPE_CHECKING:
    from bot.features.messaging import MessagingFeature

logger = logging.getLogger(__name__)

CB_PREFIX = "crt:"
CALLBACK_NEW_YES = f"{CB_PREFIX}nw:y"
CALLBACK_NEW_NO = f"{CB_PREFIX}nw:n"

# Устаревшие состояния (до сценария «сразу диалог») — просим перезапустить /new
_LEGACY_STATES = frozenset(
    {
        "pick_content_type",
        "pick_product",
        "awaiting_custom_content_type",
        "awaiting_custom_product",
        "awaiting_topic",
    }
)


class CreativeRagCoordinator:
    def __init__(self, messaging: MessagingFeature):
        self._m = messaging

    def invalidate_options_cache(self) -> None:
        """Зарезервировано."""

    async def _chroma_distinct_options(self, field: str) -> List[str]:
        vals: List[str] = []
        rag = self._rag()
        if rag is None:
            return []
        vals = await rag.retriever.distinct_expert_metadata_values_async(field)
        seen: List[str] = []
        for v in sorted({str(x).strip() for x in vals if str(x).strip()}):
            seen.append(v)
        return seen[:45]

    @property
    def _stor(self):
        return self._m.user_storage

    def _rag(self):
        bot = self._m.bot
        return getattr(bot, "rag_stack", None) if bot else None

    async def _ensure_user_row(self, user_id: int, *, from_user=None) -> None:
        """Строка в ``users`` нужна для ``token_usage`` / ``interaction_logs``."""
        if user_id <= 0:
            return
        if from_user is not None:
            await self._stor.add_or_update_user(
                {
                    "user_id": from_user.id,
                    "username": getattr(from_user, "username", None),
                    "first_name": getattr(from_user, "first_name", None),
                    "last_name": getattr(from_user, "last_name", None),
                    "language_code": getattr(from_user, "language_code", None),
                    "is_premium": getattr(from_user, "is_premium", False),
                }
            )
            return
        if not await self._stor.get_user(user_id):
            await self._stor.add_or_update_user({"user_id": user_id})

    async def on_command_new(self, message: Message, *, actor=None) -> None:
        user = actor or message.from_user
        uid = user.id if user else 0
        if actor is None:
            await self._stor.save_user_from_message(message)
        else:
            await self._ensure_user_row(uid, from_user=user)

        row = await self._stor.get_creative_session(uid)
        state = (row or {}).get("state") or CS_IDLE

        if state == CS_ACTIVE:
            await self._stor.upsert_creative_session(
                uid,
                state=CS_CONFIRM_NEW,
                product=(row or {}).get("product"),
                content_type=(row or {}).get("content_type"),
                topic=(row or {}).get("topic"),
                task_id=(row or {}).get("task_id"),
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Да, новая задача",
                            callback_data=CALLBACK_NEW_YES,
                            style="success",
                        ),
                        InlineKeyboardButton(
                            text="Отмена",
                            callback_data=CALLBACK_NEW_NO,
                            style="danger",
                        ),
                    ]
                ]
            )
            await message.answer(
                "У вас уже есть <b>активная задача</b>.\n\n"
                "Начать новую? Бот переключится на неё — в переписке старые сообщения "
                "останутся, но контекст прошлой задачи для аватара сбросится.\n\n"
                "Продолжить?",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return

        await self._start_new_task(uid, message)

    async def _start_new_task(self, user_id: int, anchor: Message) -> None:
        task_id = uuid.uuid4()
        await self._stor.upsert_creative_session(
            user_id,
            state=CS_ACTIVE,
            product=None,
            content_type=None,
            topic=None,
            task_id=task_id,
        )
        await self._ensure_user_row(user_id)
        from config import config as app_config

        if getattr(app_config, "COURSE_ENABLED", False):
            from course.products import active_product

            await anchor.answer(
                f"Контекст сброшен. Активный продукт: <b>{active_product().name}</b>.\n"
                "Напишите задачу — сразу подберу материалы и напишу черновик.",
                parse_mode=ParseMode.HTML,
            )
            return
        await self._run_opening(anchor, user_id=user_id)

    async def on_callback(self, callback: CallbackQuery) -> None:
        data = (callback.data or "").strip()
        uid = callback.from_user.id if callback.from_user else 0
        msg = callback.message

        if data == CALLBACK_NEW_YES:
            anchor = callback.message
            if anchor:
                try:
                    await anchor.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            if anchor:
                await self._ensure_user_row(uid, from_user=callback.from_user)
                await self._start_new_task(uid, anchor)
            await callback.answer()
            return

        if data == CALLBACK_NEW_NO:
            prev = await self._stor.get_creative_session(uid)
            if prev:
                await self._stor.upsert_creative_session(
                    uid,
                    state=CS_ACTIVE,
                    product=prev.get("product"),
                    content_type=prev.get("content_type"),
                    topic=prev.get("topic"),
                    task_id=prev.get("task_id"),
                )
            if msg:
                await msg.edit_reply_markup(reply_markup=None)
                await msg.answer(
                "Продолжить текущую задачу можно просто написав в чат."
                )
            await callback.answer()
            return

        await callback.answer()

    async def try_handle_private_message(self, message: Message, text: str) -> bool:
        if (text or "").strip().startswith("/"):
            return False

        uid = message.from_user.id if message.from_user else 0
        from config import config as app_config

        row = await self._stor.get_creative_session(uid)
        state = (row or {}).get("state") or CS_IDLE

        if getattr(app_config, "COURSE_ENABLED", False):
            if state != CS_ACTIVE or not row or not row.get("task_id"):
                task_id = uuid.uuid4()
                await self._stor.upsert_creative_session(
                    uid, state=CS_ACTIVE, task_id=task_id
                )
                row = await self._stor.get_creative_session(uid)
            user_msg = (text or "").strip()
            if not user_msg:
                return False
            tid = row.get("task_id")
            await self._stor.append_creative_task_turn(uid, tid, "user", user_msg)
            await self._stor.upsert_creative_session(
                uid,
                state=CS_ACTIVE,
                topic=user_msg[:2000],
                task_id=tid,
            )
            await self._run_course_task(message, user_id=uid, task_text=user_msg)
            return True

        if not row:
            return False
        if state in (CS_IDLE, CS_CONFIRM_NEW):
            return False

        if state in _LEGACY_STATES:
            await self._stor.reset_creative_session_row(uid)
            await message.answer(
                "Сценарий /new обновлён: выбор кнопками больше не нужен. "
                "Нажмите <code>/new</code> — продюсер контента спросит, что делать и по какому продукту.",
                parse_mode=ParseMode.HTML,
            )
            return True

        if state == CS_ACTIVE:
            user_msg = (text or "").strip()
            if not user_msg:
                await message.answer("Напишите текст задачи или уточнения.")
                return True
            tid = row.get("task_id")
            if not tid:
                await message.answer("Сессия сброшена. Начните с /new")
                return True
            await self._stor.append_creative_task_turn(uid, tid, "user", user_msg)
            if not (row.get("topic") or "").strip():
                await self._stor.upsert_creative_session(
                    uid,
                    state=CS_ACTIVE,
                    product=row.get("product"),
                    content_type=row.get("content_type"),
                    topic=user_msg[:2000],
                    task_id=tid,
                )
            is_first_user = await self._count_user_turns(tid) <= 1
            await self._run_generation(
                message,
                user_id=uid,
                is_revision=not is_first_user,
            )
            return True
        return False

    async def _run_course_task(self, message: Message, *, user_id: int, task_text: str) -> None:
        """Свободный запрос: поиск через шлюз + черновик."""
        from course.disk_layout import parse_lesson_key_from_text
        from course.formats import FORMATS
        from course.products import active_product_id

        studio = self._m.feature_manager.get_optional("content_studio")
        rag = self._rag()
        lesson_key = parse_lesson_key_from_text(task_text)
        fmt = "tg_post"
        low = task_text.casefold()
        if "рилс" in low or "reels" in low:
            fmt = "reels"
        elif "карусел" in low:
            fmt = "carousel"
        elif "сторис" in low or "stories" in low:
            fmt = "stories"
        lesson_id = None
        if lesson_key:
            les = await self._stor.get_course_lesson(
                product_id=active_product_id(), lesson_key=lesson_key
            )
            if les:
                lesson_id = les["id"]
        card_ids = []
        if rag is not None:
            from rag.scope import scope_from_stack

            gw = scope_from_stack(rag)
            hits = gw.search_cards(task_text, k=5, lesson_key=lesson_key or None)
            for h in hits:
                cid = (h.get("metadata") or {}).get("card_id")
                if cid:
                    try:
                        card_ids.append(uuid.UUID(str(cid)))
                    except ValueError:
                        pass
        iid = await self._stor.insert_content_item(
            product_id=active_product_id(),
            format=fmt,
            card_ids=card_ids[:3],
            lesson_id=lesson_id,
            angle=task_text[:500],
            status="drafting",
            created_by=user_id,
        )
        if studio and iid:
            await studio.draft_from_item(message.chat.id, user_id, iid)
        else:
            await message.answer("Не удалось создать черновик.")

    async def _count_user_turns(self, task_id) -> int:
        turns = await self._stor.get_creative_task_turns(
            task_id, max_messages=config.CREATIVE_TASK_HISTORY_MAX_MESSAGES
        )
        return sum(1 for t in turns if t.get("role") == "user")

    async def _run_opening(self, anchor: Message, *, user_id: int) -> None:
        rag = self._rag()
        ac = self._m.agents_client
        if ac is None:
            await anchor.answer("Аватар временно недоступен.")
            return

        products, ctypes = await asyncio.gather(
            self._chroma_distinct_options("product"),
            self._chroma_distinct_options("content_type"),
        )
        system = build_opening_system_prompt(
            known_products=products,
            known_content_types=ctypes,
        )
        messages_list = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    "[системное: /new — первый ход. Коротко поприветствуй и задай два вопроса: "
                    "что хотите сделать сейчас и по какому продукту.]"
                ),
            },
        ]
        raw = await ac.run_with_messages(messages_list, user_id)
        if not raw or not str(raw).strip():
            await anchor.answer("Не удалось получить ответ. Попробуйте описать задачу текстом.")
            return
        await self._finalize_agent_reply(
            anchor, str(raw).strip(), user_id=user_id, is_opening=True
        )

    async def _run_generation(
        self, anchor: Message, *, user_id: int, is_revision: bool
    ) -> None:
        row = await self._stor.get_creative_session(user_id)
        if not row:
            return
        topic = (row.get("topic") or "").strip()
        prod = (row.get("product") or "").strip()
        ctype = (row.get("content_type") or "").strip()
        tid = row.get("task_id")
        rag = self._rag()
        ac = self._m.agents_client

        if rag is None or ac is None:
            await anchor.answer(
                "RAG сейчас недоступен (выключено в настройках или нет API-ключа). "
                "Обратитесь к администратору."
            )
            return

        turns = await self._stor.get_creative_task_turns(
            tid, max_messages=config.CREATIVE_TASK_HISTORY_MAX_MESSAGES
        )
        user_turns = [
            (t.get("content") or "").strip()
            for t in turns
            if t.get("role") == "user" and (t.get("content") or "").strip()
        ]

        products, ctypes = await asyncio.gather(
            self._chroma_distinct_options("product"),
            self._chroma_distinct_options("content_type"),
        )

        try:
            plan_ctx = user_turns[-config.RAG_RETRIEVAL_CONTEXT_USER_TURNS :]
            retrieved, golden = await asyncio.gather(
                rag.retriever.retrieve_for_avatar_task_async(
                    user_turns=plan_ctx,
                    task_summary=topic,
                    product=prod,
                    content_type=ctype,
                    context_user_turns=config.RAG_RETRIEVAL_CONTEXT_USER_TURNS,
                    testimonial_max_chunks=config.RAG_TESTIMONIAL_MAX_CHUNKS,
                ),
                rag.golden.format_few_shot_block_filtered_async(
                    "\n".join(plan_ctx) or topic,
                    product=prod,
                    content_type=ctype,
                ),
            )
        except Exception as e:
            logger.exception("creative RAG gather failed: %s", e)
            retrieved, golden = "", ""

        system = build_dialogue_system_prompt(
            task_summary=topic,
            product=prod,
            content_type=ctype,
            retrieved_context=retrieved,
            golden_block=golden,
            known_products=products,
            known_content_types=ctypes,
            is_revision=is_revision,
        )

        messages_list = [{"role": "system", "content": system}]
        for t in turns:
            messages_list.append({"role": t["role"], "content": t["content"]})

        raw = await ac.run_with_messages(messages_list, user_id)
        if not raw or not str(raw).strip():
            await anchor.answer("Не удалось получить ответ. Попробуйте ещё раз.")
            return
        await self._finalize_agent_reply(
            anchor, str(raw).strip(), user_id=user_id, is_opening=False
        )

    async def _finalize_agent_reply(
        self,
        anchor: Message,
        raw_reply: str,
        *,
        user_id: int,
        is_opening: bool,
    ) -> None:
        row = await self._stor.get_creative_session(user_id)
        if not row:
            return

        reply_text, meta = split_agent_meta(raw_reply)
        if not reply_text.strip():
            reply_text = raw_reply
            meta = {}

        tid = row.get("task_id")
        prod = (row.get("product") or "").strip()
        ctype = (row.get("content_type") or "").strip()
        topic = (row.get("topic") or "").strip()

        if meta.get("product"):
            prod = meta["product"]
        if meta.get("content_type"):
            ctype = meta["content_type"]
        if meta.get("task_summary"):
            topic = meta["task_summary"]

        await self._stor.upsert_creative_session(
            user_id,
            state=CS_ACTIVE,
            product=prod or None,
            content_type=ctype or None,
            topic=topic or None,
            task_id=tid,
        )

        if tid:
            await self._stor.append_creative_task_turn(
                user_id, tid, "assistant", raw_reply
            )

        rag = self._rag()
        synth = f"{ctype} | {prod}" if (ctype and prod) else (ctype or prod)
        content_category = infer_content_category(ctype, synth)
        golden_topic = topic or "creative_task"
        await self._m.send_agent_reply(
            anchor,
            reply_text,
            rag_vote=rag is not None and not is_opening,
            golden_topic=golden_topic,
            golden_snapshot=GoldenSnapshot(
                source_flow=GOLDEN_FLOW_CREATIVE_TASK,
                product=prod,
                content_type=ctype,
                content_category=content_category,
                task_id=str(tid) if tid else None,
            ),
        )
