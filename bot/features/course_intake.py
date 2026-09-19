"""Приём ссылок на видео в личке, подтверждение, /queue."""

from __future__ import annotations

import logging
import uuid
from html import escape as html_escape
from typing import Any, Dict, List, Optional
from uuid import UUID

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_admin_or_super, is_super_admin_user_id
from bot.features.base import BaseFeature
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT
from config import config
from course.classify import classify_video
from course.disk_layout import lesson_sort_key
from course.llm import CourseLLM
from course.models import KIND_LABELS, VideoProbe
from course.products import active_product, active_product_id, scoped_product_ids
from course.video_hosts import adapter_for
from course.video_hosts.ytdlp import YtDlpAdapter, extract_video_urls

logger = logging.getLogger(__name__)


def _fmt_dur(sec: Optional[int]) -> str:
    if not sec:
        return "—"
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class CourseIntakeFeature(BaseFeature):
    name = "course_intake"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._pending: Dict[str, Dict[str, Any]] = {}

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.callback_query.register(
            self.on_callback, CALLBACK_PRIVATE_CHAT, F.data.startswith("in:")
        )

    async def try_handle_links(self, message: Message, text: str) -> bool:
        pairs = extract_video_urls(text or "")
        if not pairs:
            return False
        uid = message.from_user.id
        if not await is_admin_or_super(self._app.user_storage, uid):
            return False
        await message.answer("Смотрю ссылки…")
        items = []
        comment = text
        for host, url in pairs[: int(config.COURSE_PLAYLIST_MAX or 100)]:
            if host == "kinescope":
                await message.answer(
                    f"Kinescope пока на этапе 5: {html_escape(url)}",
                    parse_mode=ParseMode.HTML,
                )
                continue
            adapter = adapter_for(host)
            probes: List[VideoProbe] = []
            if "list=" in url and host == "youtube":
                probes = await YtDlpAdapter("youtube").probe_playlist(
                    url, limit=int(config.COURSE_PLAYLIST_MAX or 100)
                )
            else:
                p = await adapter.probe(url)
                if p:
                    probes = [p]
            if not probes:
                await message.answer(f"Не удалось прочитать: {url}")
                continue
            for probe in probes:
                existing = None
                if probe.video_id:
                    existing = await self._app.user_storage.get_course_source_by_video(
                        host, probe.video_id
                    )
                items.append({"host": host, "probe": probe, "existing": existing, "comment": comment})
        if not items:
            return True
        if items[0]["existing"] and len(items) == 1:
            ex = items[0]["existing"]
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔁 Переобработать",
                    callback_data=f"in:re:{ex['id']}",
                )
            ]])
            await message.answer(
                f"Уже в базе (урок {html_escape(str(ex.get('lesson_id') or '—'))}, "
                f"{ex.get('created_at')})",
                reply_markup=kb,
            )
            return True
        await self._classify_and_confirm(message, items)
        return True

    async def _classify_and_confirm(self, message: Message, items: list) -> None:
        stor = self._app.user_storage
        lessons = await stor.list_course_lessons(active_product_id())
        llm = CourseLLM(stor)
        classified = []
        for it in items:
            probe: VideoProbe = it["probe"]
            clf = await classify_video(
                probe=probe,
                comment=it.get("comment") or "",
                lessons=lessons,
                llm=llm,
                user_id=message.from_user.id,
            )
            classified.append({**it, "clf": clf})
        batch_id = uuid.uuid4().hex[:10]
        self._pending[batch_id] = {
            "user_id": message.from_user.id,
            "chat_id": message.chat.id,
            "items": classified,
        }
        product = active_product()
        lines = [f"<b>{html_escape(product.name)}</b> · подтверждение источников"]
        for i, it in enumerate(classified, 1):
            p: VideoProbe = it["probe"]
            c = it["clf"]
            host = it["host"].capitalize()
            kind = KIND_LABELS.get(c.kind, c.kind)
            lesson = f"Урок {c.lesson_key}" if c.lesson_key else "без урока"
            lines.append(
                f"{i}. 🎬 {host} · «{html_escape(p.title or '')}» · {_fmt_dur(p.duration_sec)}\n"
                f"→ {html_escape(product.name)} · {html_escape(lesson)} · {html_escape(kind)}"
            )
        text = "\n".join(lines)
        if len(classified) == 1:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Верно", callback_data=f"in:ok:{batch_id}"),
                    InlineKeyboardButton(text="📚 Другой урок", callback_data=f"in:ls:{batch_id}"),
                ],
                [
                    InlineKeyboardButton(text="🔁 Тип", callback_data=f"in:tp:{batch_id}"),
                    InlineKeyboardButton(text="✖ Отмена", callback_data=f"in:x:{batch_id}"),
                ],
            ])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Всё верно", callback_data=f"in:ok:{batch_id}"),
                    InlineKeyboardButton(text="✖ Отмена", callback_data=f"in:x:{batch_id}"),
                ]
            ])
        # оценка
        hours = sum((it["probe"].duration_sec or 0) for it in classified) / 3600.0
        if hours > 0.2 or len(classified) > 3:
            costs = config.cost_table_map
            whisper = float(costs.get("whisper_per_min") or 0.006) * hours * 60
            text += (
                f"\n\nОценка: ~{hours:.1f} ч видео. "
                f"Субтитры: смотрим при обработке. Whisper при необходимости ~${whisper:.2f}."
            )
            kb.inline_keyboard.append([
                InlineKeyboardButton(text="▶️ Запустить", callback_data=f"in:ok:{batch_id}")
            ])
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def on_callback(self, callback: CallbackQuery) -> None:
        data = (callback.data or "").split(":")
        if len(data) < 3:
            await callback.answer()
            return
        _, action, rest = data[0], data[1], ":".join(data[2:])
        if action == "re":
            await self._reprocess(callback, rest)
            return
        if action == "rq":
            await self._retry_queue(callback, rest)
            return
        batch = self._pending.get(rest.split(":")[0] if action != "ok" else rest)
        if action in ("ok", "x", "ls", "tp"):
            batch = self._pending.get(rest)
        if not batch and action not in ("k", "t"):
            await callback.answer("Сессия устарела", show_alert=True)
            return
        if action == "x":
            self._pending.pop(rest, None)
            await callback.message.edit_text("Отменено.")
            await callback.answer()
            return
        if action == "ok":
            await self._accept_batch(callback, rest)
            return
        if action == "ls":
            await self._show_lessons(callback, rest)
            return
        if action == "tp":
            await self._show_types(callback, rest)
            return
        if action == "k":
            # in:k:batch:lesson_key
            parts = rest.split(":", 1)
            if len(parts) == 2:
                await self._set_lesson(callback, parts[0], parts[1])
            return
        if action == "t":
            parts = rest.split(":", 1)
            if len(parts) == 2:
                await self._set_kind(callback, parts[0], parts[1])
            return
        await callback.answer()

    async def _accept_batch(self, callback: CallbackQuery, batch_id: str) -> None:
        batch = self._pending.pop(batch_id, None)
        if not batch:
            await callback.answer("Уже обработано")
            return
        stor = self._app.user_storage
        product = active_product()
        created = 0
        for it in batch["items"]:
            probe: VideoProbe = it["probe"]
            clf = it["clf"]
            lesson_id = None
            if clf.lesson_key:
                a, b = lesson_sort_key(clf.lesson_key)
                title = probe.title or ""
                existing_l = await stor.get_course_lesson(
                    product_id=product.id, lesson_key=clf.lesson_key
                )
                if not existing_l:
                    lesson_id = await stor.upsert_course_lesson(
                        product_id=product.id,
                        lesson_key=clf.lesson_key,
                        lesson_no=b or a,
                        module_no=a if b else None,
                        title=title,
                    )
                else:
                    lesson_id = existing_l["id"]
            # duplicate duration check
            if lesson_id and probe.duration_sec:
                dup = await stor.find_similar_video_source(
                    product_id=product.id,
                    kind=clf.kind,
                    lesson_id=lesson_id,
                    duration_sec=probe.duration_sec,
                )
                if dup:
                    await stor.append_source_alt_url(dup["id"], probe.url)
                    continue
            sid = await stor.insert_course_source(
                product_id=product.id,
                origin=it["host"],
                kind=clf.kind,
                lesson_id=lesson_id,
                title=probe.title or probe.url,
                url=probe.url,
                video_id=probe.video_id or None,
                duration_sec=probe.duration_sec,
                recorded_on=clf.recorded_on or probe.recorded_on,
                status="new",
                added_by=callback.from_user.id,
                intake_chat_id=callback.message.chat.id,
                intake_message_id=callback.message.message_id,
            )
            if sid:
                created += 1
        try:
            await callback.message.edit_text(
                f"⏳ в очереди: {created}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await callback.answer("В очередь")

    async def _show_lessons(self, callback: CallbackQuery, batch_id: str) -> None:
        lessons = await self._app.user_storage.list_course_lessons(active_product_id())
        rows = []
        row = []
        for les in lessons[:30]:
            row.append(InlineKeyboardButton(
                text=les["lesson_key"],
                callback_data=f"in:k:{batch_id}:{les['lesson_key']}"[:64],
            ))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data=f"in:ok:{batch_id}")])
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer()

    async def _show_types(self, callback: CallbackQuery, batch_id: str) -> None:
        rows = [[
            InlineKeyboardButton(text="Урок", callback_data=f"in:t:{batch_id}:lesson_video"),
            InlineKeyboardButton(text="Практика", callback_data=f"in:t:{batch_id}:practice"),
        ], [
            InlineKeyboardButton(text="Эфир", callback_data=f"in:t:{batch_id}:broadcast"),
            InlineKeyboardButton(text="Другое", callback_data=f"in:t:{batch_id}:other"),
        ]]
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await callback.answer()

    async def _set_lesson(self, callback: CallbackQuery, batch_id: str, key: str) -> None:
        batch = self._pending.get(batch_id)
        if not batch:
            await callback.answer("Сессия устарела", show_alert=True)
            return
        for it in batch["items"]:
            it["clf"].lesson_key = key
        await callback.answer(f"Урок {key}")
        # re-show confirm - simplified: accept
        await self._accept_batch(callback, batch_id)

    async def _set_kind(self, callback: CallbackQuery, batch_id: str, kind: str) -> None:
        batch = self._pending.get(batch_id)
        if not batch:
            await callback.answer("Сессия устарела", show_alert=True)
            return
        for it in batch["items"]:
            it["clf"].kind = kind
        await callback.answer("Тип обновлён")
        await self._accept_batch(callback, batch_id)

    async def _reprocess(self, callback: CallbackQuery, source_id: str) -> None:
        try:
            sid = UUID(source_id)
        except ValueError:
            await callback.answer("Некорректный id")
            return
        await self._app.user_storage.update_course_source(
            sid, status="new", attempts=0, error_message=""
        )
        rs = getattr(self._app, "rag_stack", None)
        if rs is not None:
            rs.materials.delete_by_source(str(sid))
        await self._app.user_storage.archive_cards_for_source(sid)
        await callback.answer("В очередь")

    async def cmd_queue(self, message: Message) -> None:
        await self.show_queue(message, user_id=message.from_user.id)

    async def show_queue(self, message: Message, *, user_id: int) -> None:
        if not await is_admin_or_super(self._app.user_storage, user_id):
            return
        rows = await self._app.user_storage.list_course_queue(scoped_product_ids())
        if not rows:
            await message.answer("Очередь пуста.")
            return
        lines = ["<b>Очередь обработки</b>"]
        kb_rows = []
        for r in rows[:20]:
            st = r.get("status")
            title = html_escape((r.get("title") or "")[:60])
            lines.append(f"• {st} · {title}")
            if st == "error":
                kb_rows.append([InlineKeyboardButton(
                    text=f"🔁 {title[:20]}",
                    callback_data=f"in:rq:{r['id']}",
                )])
        await message.answer(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None,
        )

    async def _retry_queue(self, callback: CallbackQuery, source_id: str) -> None:
        try:
            sid = UUID(source_id)
        except ValueError:
            await callback.answer()
            return
        await self._app.user_storage.update_course_source(
            sid, status="new", attempts=0, next_attempt_at=None, error_message=""
        )
        await callback.answer("Повтор")

    async def cmd_remine(self, message: Message) -> None:
        uid = message.from_user.id
        if not is_super_admin_user_id(uid):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /remine &lt;source_uuid|lesson_key&gt;", parse_mode=ParseMode.HTML)
            return
        arg = parts[1].strip()
        worker = getattr(self._app, "course_worker", None)
        if not worker:
            await message.answer("Воркер не запущен")
            return
        try:
            sid = UUID(arg)
            await worker.remine(sid)
            await message.answer("Переразбор источника запущен")
            return
        except ValueError:
            pass
        les = await self._app.user_storage.get_course_lesson(
            product_id=active_product_id(), lesson_key=arg
        )
        if not les:
            await message.answer("Не найдено")
            return
        srcs = await self._app.user_storage.list_course_sources_for_lesson(les["id"])
        for s in srcs:
            await worker.remine(s["id"])
        await message.answer(f"Переразбор урока {arg}: {len(srcs)} источников")
