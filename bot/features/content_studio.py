"""План, уроки, банк карточек, черновики и кнопки, дайджесты."""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from html import escape as html_escape
from pathlib import Path
from typing import Any, List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.admin_guard import is_admin_or_super, is_super_admin_user_id
from bot.features.base import BaseFeature
from bot.features.golden_chroma_fields import GoldenSnapshot, GOLDEN_FLOW_CREATIVE_TASK
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT
from bot.states import DislikeReasonStates
from bot.utils.telegram_chunk import split_telegram_html_chunks
from config import config
from course.formats import FORMATS, get_format
from course.llm import CourseLLM
from course.mining import window_for_card
from course.paths import source_dir
from course.planner import build_week_plan, seconds_until, week_start_for
from course.products import active_product, active_product_id, product_display_name
from course.speech import segments_from_dicts
from course.writer import write_draft
from rag.scope import scope_from_stack

logger = logging.getLogger(__name__)

_DOWN_REASONS = [
    ("tone", "Не мой тон"),
    ("water", "Вода"),
    ("fact", "Неточно по смыслу"),
    ("long", "Длинно"),
    ("other", "Другое — напишу"),
]


def _ci(action: str, item_id: UUID) -> str:
    return f"ci:{action}:{item_id}"[:64]


class ContentStudioFeature(BaseFeature):
    name = "content_studio"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._plan_task = None
        self._plan_toggles: dict[str, set] = {}

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.callback_query.register(
            self.on_ci, CALLBACK_PRIVATE_CHAT, F.data.startswith("ci:")
        )
        dispatcher.callback_query.register(
            self.on_pl, CALLBACK_PRIVATE_CHAT, F.data.startswith("pl:")
        )
        dispatcher.callback_query.register(
            self.on_bk, CALLBACK_PRIVATE_CHAT, F.data.startswith("bk:")
        )

    async def start_background_tasks(self) -> None:
        import asyncio

        if self._plan_task and not self._plan_task.done():
            return
        self._plan_task = asyncio.create_task(self._weekly_loop(), name="content_plan")

    async def stop_background_tasks(self) -> None:
        if self._plan_task and not self._plan_task.done():
            self._plan_task.cancel()

    async def _weekly_loop(self) -> None:
        import asyncio

        while True:
            try:
                delay = seconds_until(
                    int(config.CONTENT_PLAN_HOUR),
                    0,
                    weekday=int(config.CONTENT_PLAN_WEEKDAY),
                    tz_name=config.CONTENT_PLAN_TZ,
                )
                logger.info("[%s] sleep %.0fs until weekly plan", self.name, delay)
                await asyncio.sleep(delay)
                await self._run_plan_for_admins()
                await asyncio.sleep(70)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("weekly plan: %s", e)
                await asyncio.sleep(300)

    async def _run_plan_for_admins(self) -> None:
        sid = int(config.SUPER_ADMIN_ID or 0)
        if sid:
            await self.build_and_send_plan(sid, sid)

    def _stor(self):
        return self._app.user_storage

    async def notify_admins(self, text: str, lesson_id: Optional[int] = None) -> None:
        sid = int(config.SUPER_ADMIN_ID or 0)
        if not sid:
            return
        kb = None
        if lesson_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📖 Паспорт", callback_data=f"bk:ps:{lesson_id}"),
                InlineKeyboardButton(text="📝 Серия постов", callback_data=f"bk:sr:{lesson_id}"),
            ]])
        try:
            await self._app.bot.send_message(sid, text, reply_markup=kb)
        except Exception as e:
            logger.warning("notify_admins: %s", e)

    async def cmd_lessons(self, message: Message) -> None:
        if not await is_admin_or_super(self._stor(), message.from_user.id):
            return
        await self.show_lessons(
            message.chat.id,
            message.from_user.id,
            bot=message.bot,
        )

    async def show_lessons(
        self,
        chat_id: int,
        user_id: int,
        *,
        bot,
        reply_markup=None,
    ) -> None:
        product = active_product()
        lessons = await self._stor().list_course_lessons(product.id)
        if not lessons:
            await bot.send_message(chat_id, f"{product.name}: уроков пока нет.", reply_markup=reply_markup)
            return
        lines = [f"<b>{html_escape(product.name)}</b> · уроки"]
        kb_rows = []
        for les in lessons:
            srcs = await self._stor().list_course_sources_for_lesson(les["id"])
            flags = []
            if any(s.get("kind") == "summary" and s.get("status") == "done" for s in srcs):
                flags.append("📄")
            if any(s.get("kind") == "slides" and s.get("status") == "done" for s in srcs):
                flags.append("🖼")
            if any(s.get("kind") == "lesson_video" and s.get("status") == "done" for s in srcs):
                flags.append("🎬")
            prac = sum(1 for s in srcs if s.get("kind") == "practice" and s.get("status") == "done")
            if prac:
                flags.append(f"🧩{prac}")
            cards = await self._stor().list_content_cards(
                product_id=product.id, lesson_id=les["id"], limit=200
            )
            flags.append(f"💡{len(cards)}")
            title = html_escape(les.get("title") or "")
            key = les["lesson_key"]
            lines.append(f"{key} {title}  " + " ".join(flags))
            kb_rows.append([InlineKeyboardButton(
                text=f"📖 {key}",
                callback_data=f"mn:les:{key}"[:64],
            )])
        if reply_markup and getattr(reply_markup, "inline_keyboard", None):
            kb_rows.extend(reply_markup.inline_keyboard)
        await bot.send_message(
            chat_id,
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )

    async def cmd_lesson(self, message: Message) -> None:
        if not await is_admin_or_super(self._stor(), message.from_user.id):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Выберите урок в меню → Уроки.")
            return
        await self.show_lesson(message.chat.id, message.from_user.id, parts[1].strip())

    async def show_lesson(self, chat_id: int, user_id: int, key: str) -> None:
        les = await self._stor().get_course_lesson(product_id=active_product_id(), lesson_key=key)
        if not les:
            await self._app.bot.send_message(chat_id, f"Урок {key} не найден")
            return
        text = (les.get("passport_text") or "Паспорт ещё не собран.").strip()
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💡 Идеи", callback_data=f"bk:id:{les['id']}"),
            InlineKeyboardButton(text="📝 Серия постов", callback_data=f"bk:sr:{les['id']}"),
            InlineKeyboardButton(text="🎠 Карусель", callback_data=f"bk:cr:{les['id']}"),
        ], [
            InlineKeyboardButton(text="← Меню", callback_data="mn:home"),
        ]])
        for chunk in split_telegram_html_chunks(html_escape(text) if "<" not in text[:20] else text):
            await self._app.bot.send_message(chat_id, chunk, reply_markup=kb)

    async def cmd_bank(self, message: Message) -> None:
        if not await is_admin_or_super(self._stor(), message.from_user.id):
            return
        arg = ((message.text or "").split(maxsplit=1) + [""])[1].strip()
        await self.show_bank(message.chat.id, message.from_user.id, arg=arg)

    async def show_bank(self, chat_id: int, user_id: int, *, arg: str = "") -> None:
        product_id = active_product_id()
        lesson_id = None
        card_type = None
        if arg:
            les = await self._stor().get_course_lesson(product_id=product_id, lesson_key=arg)
            if les:
                lesson_id = les["id"]
            else:
                card_type = arg
        cards = await self._stor().list_content_cards(
            product_id=product_id, lesson_id=lesson_id, card_type=card_type, limit=15
        )
        if not cards:
            await self._app.bot.send_message(
                chat_id,
                "Карточек нет.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="← Меню", callback_data="mn:home")]]
                ),
            )
            return
        for c in cards:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📝 Пост", callback_data=f"bk:w:{c['id']}:tg_post"),
                InlineKeyboardButton(text="🎬 Рилс", callback_data=f"bk:w:{c['id']}:reels"),
                InlineKeyboardButton(text="🎠 Карусель", callback_data=f"bk:w:{c['id']}:carousel"),
            ]])
            await self._app.bot.send_message(
                chat_id,
                f"<b>{html_escape(c['title'])}</b>\n{html_escape(c['text'][:600])}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )

    async def cmd_focus(self, message: Message) -> None:
        if not await is_admin_or_super(self._stor(), message.from_user.id):
            return
        arg = ((message.text or "").split(maxsplit=1) + [""])[1].strip()
        pid = active_product_id()
        if not arg:
            val = await self._stor().get_content_setting(pid, "focus")
            await message.answer(f"Фокус: {val or 'не задан'}")
            return
        if arg.lower() in ("off", "нет", "сброс"):
            await self._stor().set_content_setting(pid, "focus", "")
            await message.answer("Фокус сброшен.")
            return
        await self._stor().set_content_setting(pid, "focus", arg)
        await message.answer(f"Фокус: {arg}")

    async def cmd_plan(self, message: Message) -> None:
        if not await is_admin_or_super(self._stor(), message.from_user.id):
            return
        await message.answer("Собираю план…")
        await self.build_and_send_plan(message.chat.id, message.from_user.id)

    async def build_and_send_plan(self, chat_id: int, user_id: int) -> None:
        pid = active_product_id()
        product = active_product()
        mix = config.content_plan_mix_map
        focus = await self._stor().get_content_setting(pid, "focus") or ""
        if isinstance(focus, dict):
            focus = str(focus)
        cards = await self._stor().pick_plan_candidates(
            product_id=pid, reuse_days=int(config.CARD_REUSE_DAYS), limit=40
        )
        for c in cards:
            if c.get("lesson_id"):
                les = await self._stor().get_course_lesson_by_id(c["lesson_id"])
                c["lesson_key"] = (les or {}).get("lesson_key")
        llm = CourseLLM(self._stor())
        items = await build_week_plan(
            cards=cards, mix=mix, focus=str(focus or ""), llm=llm, user_id=user_id
        )
        tz = ZoneInfo(config.CONTENT_PLAN_TZ)
        today = date.today()
        start = week_start_for(today, int(config.CONTENT_PLAN_WEEKDAY))
        plan_id = await self._stor().insert_content_plan(
            product_id=pid,
            week_start=start,
            focus=str(focus or ""),
            mix=mix,
            created_by=user_id,
        )
        created = []
        for it in items:
            iid = await self._stor().insert_content_item(
                product_id=pid,
                plan_id=plan_id,
                format=it["format"],
                card_ids=it["card_ids"],
                lesson_id=it.get("lesson_id"),
                angle=it.get("angle") or "",
                goal=it.get("funnel_stage") or "",
                planned_day=it.get("planned_day"),
                status="planned",
                created_by=user_id,
            )
            if iid:
                created.append(iid)
        days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        end = start + timedelta(days=6)
        lines = [
            f"<b>{html_escape(product.name)} · план на {start.strftime('%d.%m')}–{end.strftime('%d.%m')}</b>"
        ]
        if focus:
            lines.append(f"Фокус: {html_escape(str(focus))}")
        plan_items = await self._stor().list_plan_items(plan_id)
        self._plan_toggles[str(plan_id)] = set()
        kb_rows = []
        for i, it in enumerate(plan_items, 1):
            d = days[it["planned_day"]] if it.get("planned_day") is not None else "—"
            spec = get_format(it["format"])
            lines.append(
                f"{i}. {d} · {spec.title if spec else it['format']} · {html_escape(it.get('angle') or '')}"
            )
            kb_rows.append([InlineKeyboardButton(
                text=f"✅ {i}",
                callback_data=f"pl:tg:{plan_id}:{it['id']}",
            )])
        kb_rows.append([
            InlineKeyboardButton(text="🔄 Заменить ❌", callback_data=f"pl:rp:{plan_id}"),
            InlineKeyboardButton(text="✍️ Написать выбранные", callback_data=f"pl:wr:{plan_id}"),
        ])
        msg = await self._app.bot.send_message(
            chat_id,
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )
        await self._stor().update_content_plan_message(
            plan_id, chat_id=chat_id, message_id=msg.message_id
        )

    async def cmd_costs(self, message: Message) -> None:
        if not is_super_admin_user_id(message.from_user.id):
            return
        days = 7
        parts = (message.text or "").split()
        if len(parts) > 1:
            try:
                days = max(1, min(90, int(parts[1])))
            except ValueError:
                pass
        await self.show_costs(message, days=days)

    async def show_costs(self, message: Message, *, days: int = 7) -> None:
        async with self._stor().get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(request_kind, 'unknown') AS k,
                       COALESCE(model, '') AS m,
                       COUNT(*) AS n,
                       COALESCE(SUM(prompt_tokens),0) AS pt,
                       COALESCE(SUM(completion_tokens),0) AS ct
                  FROM token_usage
                 WHERE created_at > NOW() - ($1 || ' days')::interval
                 GROUP BY 1, 2
                 ORDER BY n DESC
                 LIMIT 30
                """,
                str(days),
            )
        if not rows:
            await message.answer("Расходов нет.")
            return
        lines = [f"<b>Расходы за {days} дн.</b>"]
        for r in rows:
            lines.append(
                f"• {html_escape(r['k'])} / {html_escape(r['m'])}: {r['n']} вызовов, "
                f"{r['pt']}+{r['ct']} ток."
            )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    async def on_pl(self, callback: CallbackQuery) -> None:
        parts = (callback.data or "").split(":")
        if len(parts) < 3:
            await callback.answer()
            return
        _, action, plan_id = parts[0], parts[1], parts[2]
        skipped = self._plan_toggles.setdefault(plan_id, set())
        if action == "tg" and len(parts) >= 4:
            iid = parts[3]
            if iid in skipped:
                skipped.discard(iid)
                await self._stor().update_content_item(UUID(iid), status="planned")
            else:
                skipped.add(iid)
                await self._stor().update_content_item(UUID(iid), status="skipped")
            await callback.answer("Ок")
            return
        if action == "wr":
            items = await self._stor().list_plan_items(UUID(plan_id))
            chosen = [it for it in items if str(it["id"]) not in skipped and it.get("status") != "skipped"]
            await callback.answer(f"Пишу {len(chosen)}")
            for it in chosen:
                await self.draft_from_item(callback.message.chat.id, callback.from_user.id, it["id"])
            return
        await callback.answer()

    async def on_bk(self, callback: CallbackQuery) -> None:
        parts = (callback.data or "").split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action == "w" and len(parts) >= 4:
            await self.draft_from_card(
                callback.message.chat.id,
                callback.from_user.id,
                UUID(parts[2]),
                parts[3],
            )
            await callback.answer()
            return
        if action == "id" and len(parts) >= 3:
            cards = await self._stor().list_content_cards(
                product_id=active_product_id(), lesson_id=int(parts[2]), limit=10
            )
            await callback.message.answer(
                "\n".join(f"• {c['title']}" for c in cards) or "нет карточек"
            )
            await callback.answer()
            return
        if action == "sr" and len(parts) >= 3:
            cards = await self._stor().list_content_cards(
                product_id=active_product_id(), lesson_id=int(parts[2]), limit=5
            )
            for c in cards[:3]:
                await self.draft_from_card(
                    callback.message.chat.id, callback.from_user.id, c["id"], "tg_post"
                )
            await callback.answer()
            return
        if action == "cr" and len(parts) >= 3:
            cards = await self._stor().list_content_cards(
                product_id=active_product_id(), lesson_id=int(parts[2]), limit=3
            )
            if cards:
                await self.draft_from_card(
                    callback.message.chat.id, callback.from_user.id, cards[0]["id"], "carousel"
                )
            await callback.answer()
            return
        if action == "ps" and len(parts) >= 3:
            les = await self._stor().get_course_lesson_by_id(int(parts[2]))
            if les:
                await callback.message.answer(les.get("passport_text") or "нет паспорта")
            await callback.answer()
            return
        await callback.answer()

    async def on_ci(self, callback: CallbackQuery, state: FSMContext) -> None:
        parts = (callback.data or "").split(":")
        if len(parts) < 3:
            await callback.answer()
            return
        action, iid = parts[1], parts[2]
        try:
            item_id = UUID(iid)
        except ValueError:
            await callback.answer()
            return
        item = await self._stor().get_content_item(item_id)
        if not item:
            await callback.answer("Черновик не найден", show_alert=True)
            return
        ver = await self._stor().get_latest_content_version(item_id)
        uid = callback.from_user.id
        if action == "up":
            await self._stor().update_content_item(item_id, status="approved")
            await self._stor().insert_content_feedback(
                item_id=item_id, version=item.get("current_version") or 0,
                user_id=uid, kind="up",
            )
            await self._stor().mark_cards_used(item.get("card_ids") or [])
            if ver and self._app.rag_stack:
                await self._app.rag_stack.golden.add_example_async(
                    item.get("angle") or ver["text"][:200],
                    ver["text"],
                    extra_metadata={
                        "schema_v": 2,
                        "product_id": item["product_id"],
                        "legacy": False,
                        "format": item["format"],
                        "lesson_key": "",
                        "seed": False,
                    },
                )
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="📌 Опубликовал", callback_data=_ci("pub", item_id)),
                    ]])
                )
            except Exception:
                pass
            await callback.answer("В золотой фонд")
            return
        if action == "dn":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data=f"ci:rs:{item_id}:{code}")]
                for code, label in _DOWN_REASONS
            ])
            await callback.message.answer("Почему не зашло?", reply_markup=kb)
            await callback.answer()
            return
        if action == "rs":
            code = parts[3] if len(parts) > 3 else ""
            if code == "other":
                await state.set_state(DislikeReasonStates.waiting_text)
                await state.update_data(item_id=str(item_id), version=item.get("current_version") or 0)
                await callback.message.answer("Напишите причину одним сообщением.")
                await callback.answer()
                return
            await self._stor().update_content_item(item_id, status="rejected")
            await self._stor().insert_content_feedback(
                item_id=item_id, version=item.get("current_version") or 0,
                user_id=uid, kind="down", reason_code=code,
            )
            await callback.answer("Записал")
            await self._maybe_fold_feedback()
            return
        if action == "pub":
            await self._stor().update_content_item(item_id, status="published")
            await self._stor().insert_content_feedback(
                item_id=item_id, version=item.get("current_version") or 0,
                user_id=uid, kind="published",
            )
            await callback.answer("Отмечено")
            return
        if action == "nv":
            await self.redraft(callback.message.chat.id, uid, item_id, "ещё один вариант, другой заход")
            await callback.answer()
            return
        if action == "sh":
            await self.redraft(callback.message.chat.id, uid, item_id, "сделай короче, убери воду")
            await callback.answer()
            return
        if action == "hk":
            await self.redraft(callback.message.chat.id, uid, item_id, "сильнее крючок в первой строке")
            await callback.answer()
            return
        if action in ("tg", "re", "cr"):
            fmt = {"tg": "tg_post", "re": "reels", "cr": "carousel"}[action]
            await self._stor().update_content_item(item_id, format=fmt)
            await self.redraft(callback.message.chat.id, uid, item_id, f"перепиши в формате {fmt}")
            await callback.answer()
            return
        await callback.answer()

    async def try_handle_draft_reply(self, message: Message, text: str) -> bool:
        reply = message.reply_to_message
        if not reply:
            return False
        found = await self._stor().find_item_by_reply(message.chat.id, reply.message_id)
        if not found:
            return False
        await self.redraft(
            message.chat.id,
            message.from_user.id,
            found["id"],
            text,
        )
        return True

    async def try_handle_dislike_text(self, message: Message, state, text: str) -> bool:
        data = await state.get_data()
        item_id = data.get("item_id")
        if not item_id:
            return False
        await self._stor().insert_content_feedback(
            item_id=UUID(item_id),
            version=int(data.get("version") or 0),
            user_id=message.from_user.id,
            kind="down",
            reason_code="other",
            reason_text=text,
        )
        await self._stor().update_content_item(UUID(item_id), status="rejected")
        await state.clear()
        await message.answer("Записал.")
        await self._maybe_fold_feedback()
        return True

    async def _maybe_fold_feedback(self) -> None:
        n = await self._stor().count_unfolded_feedback()
        if n < 10:
            return
        rows = await self._stor().list_unfolded_feedback(limit=20)
        from course.style import fold_feedback_rules

        reasons = [r.get("reason_text") or r.get("reason_code") or "" for r in rows]
        llm = CourseLLM(self._stor())
        rules = await fold_feedback_rules(reasons, llm, 0)
        if rules:
            md = "\n".join(f"- {x}" for x in rules)
            await self._stor().append_style_rules(active_product_id(), md)
            await self._stor().mark_feedback_folded([r["id"] for r in rows])

    async def draft_from_card(self, chat_id: int, user_id: int, card_id: UUID, fmt: str) -> None:
        card = await self._stor().get_content_card(card_id)
        if not card:
            return
        iid = await self._stor().insert_content_item(
            product_id=active_product_id(),
            format=fmt,
            card_ids=[card_id],
            lesson_id=card.get("lesson_id"),
            angle=card.get("title") or "",
            status="drafting",
            created_by=user_id,
        )
        if iid:
            await self.draft_from_item(chat_id, user_id, iid)

    async def draft_from_item(self, chat_id: int, user_id: int, item_id: UUID) -> None:
        item = await self._stor().get_content_item(item_id)
        if not item:
            return
        await self._stor().update_content_item(item_id, status="drafting")
        text, model, issues = await self._generate(item, user_id, instruction="")
        await self._save_and_send(chat_id, item, text, model, issues, instruction="")

    async def redraft(self, chat_id: int, user_id: int, item_id: UUID, instruction: str) -> None:
        item = await self._stor().get_content_item(item_id)
        if not item:
            return
        prev = await self._stor().get_latest_content_version(item_id)
        text, model, issues = await self._generate(
            item, user_id, instruction=instruction, previous=(prev or {}).get("text") or ""
        )
        await self._save_and_send(chat_id, item, text, model, issues, instruction=instruction)

    async def _generate(self, item: dict, user_id: int, *, instruction: str, previous: str = ""):
        from course.products import EXPERT_PRODUCT_ID

        pid = item["product_id"]
        style = await self._stor().get_active_style_profile(pid)
        style_text = (style or {}).get("text") or ""
        expert_info = await self._info_text(EXPERT_PRODUCT_ID, "expert_info")
        product_info = await self._info_text(pid, "product_info")
        cards = await self._stor().list_cards_by_ids(item.get("card_ids") or [])
        material_parts = []
        for c in cards[:3]:
            material_parts.append(f"### {c.get('title')}\n{c.get('text')}\nЦитата: {c.get('quote') or '—'}")
            src_kind = ""
            if c.get("source_id"):
                src = await self._stor().get_course_source(c["source_id"])
                src_kind = (src or {}).get("kind") or ""
                if src_kind in ("lesson_video", "broadcast") and c.get("anchor_sec") is not None:
                    tr = source_dir(c["source_id"]) / "transcript.json"
                    if tr.is_file():
                        import json
                        segs = segments_from_dicts(json.loads(tr.read_text())["segments"])
                        material_parts.append(window_for_card(segs, c, pad_sec=60))
        if item.get("lesson_id"):
            les = await self._stor().get_course_lesson_by_id(item["lesson_id"])
            if les and les.get("passport_text"):
                material_parts.append("Паспорт урока:\n" + les["passport_text"][:2500])
        golden = ""
        if self._app.rag_stack:
            gw = scope_from_stack(self._app.rag_stack)
            hits = gw.golden_examples(item.get("angle") or cards[0]["title"] if cards else "пост", item["format"], k=2)
            golden = "\n\n".join(
                (h.get("metadata") or {}).get("answer") or "" for h in hits
            )
        task = item.get("angle") or "напиши черновик"
        if item.get("goal"):
            task += f"\nЭтап воронки: {item['goal']}"
        llm = CourseLLM(self._stor())
        messaging = self._app.feature_manager.get_optional("messaging")
        agents = getattr(messaging, "agents_client", None) if messaging else None
        return await write_draft(
            format_id=item["format"],
            expert_info=expert_info[: config.COURSE_INFO_MAX_CHARS],
            product_info=product_info[: config.COURSE_INFO_MAX_CHARS],
            style_text=style_text,
            golden=golden,
            material="\n\n".join(material_parts),
            task=task,
            llm=llm,
            user_id=user_id,
            previous=previous,
            instruction=instruction,
            agents_client=agents,
        )

    async def _info_text(self, product_id: str, kind: str) -> str:
        rows = await self._stor().list_course_sources_by_product([product_id], statuses=["done"])
        for r in rows:
            if r.get("kind") == kind:
                p = source_dir(r["id"]) / "pages.json"
                if p.is_file():
                    import json
                    data = json.loads(p.read_text(encoding="utf-8"))
                    return "\n\n".join(x.get("text") or "" for x in data)
        return ""

    async def _save_and_send(
        self, chat_id: int, item: dict, text: str, model: str, issues: list, instruction: str
    ) -> None:
        ver_no = int(item.get("current_version") or 0) + 1
        await self._stor().insert_content_version(
            item_id=item["id"],
            version=ver_no,
            text=text,
            instruction=instruction,
            model=model,
        )
        await self._stor().update_content_item(
            item["id"], status="draft", current_version=ver_no
        )
        prefix = ""
        if issues:
            prefix = "⚠️ " + "; ".join(issues) + "\n\n"
        body = prefix + text
        spec = get_format(item["format"])
        parse = ParseMode.HTML if spec and spec.markup == "telegram_html" else None
        chunks = split_telegram_html_chunks(body)
        kb = self._draft_keyboard(item["id"])
        ids = []
        for i, ch in enumerate(chunks):
            msg = await self._app.bot.send_message(
                chat_id, ch, parse_mode=parse, reply_markup=kb if i == len(chunks) - 1 else None
            )
            ids.append(msg.message_id)
        await self._stor().update_version_message_ids(
            item["id"], ver_no, chat_id=chat_id, message_ids=ids
        )
        if item["format"] == "carousel":
            await self._send_slide_album(chat_id, item)

    async def _send_slide_album(self, chat_id: int, item: dict) -> None:
        from aiogram.types import InputMediaPhoto

        if not item.get("lesson_id"):
            return
        srcs = await self._stor().list_course_sources_for_lesson(item["lesson_id"])
        pngs: List[Path] = []
        for s in srcs:
            if s.get("kind") != "slides":
                continue
            d = source_dir(s["id"]) / "slides"
            if d.is_dir():
                pngs.extend(sorted(d.glob("*.png"))[:10])
        if not pngs:
            return
        media = []
        files = []
        for p in pngs[:10]:
            data = p.read_bytes()
            media.append(InputMediaPhoto(media=BufferedInputFile(data, filename=p.name)))
        try:
            await self._app.bot.send_media_group(chat_id, media=media)
        except Exception as e:
            logger.warning("slides album: %s", e)

    def _draft_keyboard(self, item_id: UUID) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Беру", callback_data=_ci("up", item_id)),
                InlineKeyboardButton(text="👎", callback_data=_ci("dn", item_id)),
            ],
            [
                InlineKeyboardButton(text="🔄 Ещё вариант", callback_data=_ci("nv", item_id)),
                InlineKeyboardButton(text="✂️ Короче", callback_data=_ci("sh", item_id)),
                InlineKeyboardButton(text="🪝 Сильнее крючок", callback_data=_ci("hk", item_id)),
            ],
            [
                InlineKeyboardButton(text="📝 В пост", callback_data=_ci("tg", item_id)),
                InlineKeyboardButton(text="🎬 В рилс", callback_data=_ci("re", item_id)),
                InlineKeyboardButton(text="🎠 В карусель", callback_data=_ci("cr", item_id)),
            ],
        ])

    async def send_practice_digest(self, src: dict) -> None:
        product = active_product()
        lesson = ""
        if src.get("lesson_id"):
            les = await self._stor().get_course_lesson_by_id(src["lesson_id"])
            if les:
                lesson = f"Урок {les['lesson_key']} «{les.get('title') or ''}»"
        cards = await self._stor().list_content_cards(
            product_id=src["product_id"], lesson_id=src.get("lesson_id"), limit=8
        )
        lines = [
            f"🧩 {product.name} · {src.get('title') or 'Практика'} · {lesson} — {len(cards)} тем для контента"
        ]
        kb_rows = []
        for i, c in enumerate(cards[:6], 1):
            extra = f" (звучал уже на {c.get('frequency')} практиках)" if c.get("frequency", 1) > 1 else ""
            lines.append(f"{i}. {c.get('type')}: «{c.get('title')}»{extra}")
            kb_rows.append([
                InlineKeyboardButton(text=f"{i} 📝", callback_data=f"bk:w:{c['id']}:tg_post"),
                InlineKeyboardButton(text="🎬", callback_data=f"bk:w:{c['id']}:reels"),
                InlineKeyboardButton(text="🎠", callback_data=f"bk:w:{c['id']}:carousel"),
            ])
        sid = int(config.SUPER_ADMIN_ID or 0)
        if not sid:
            return
        await self._app.bot.send_message(
            sid,
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )

    async def ask_lesson_for_files(self, items: list) -> None:
        sid = int(config.SUPER_ADMIN_ID or 0)
        if not sid:
            return
        names = ", ".join(it.remote.name for it in items[:8])
        await self._app.bot.send_message(
            sid,
            f"Не удалось определить урок для файлов: {names}\n"
            "Напишите ключ урока (например 2.4) или положите файлы в папку урока.",
        )
