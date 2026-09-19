"""Фоновый опрос Яндекс.Диска и команда /sync."""

from __future__ import annotations

import asyncio
import logging
from html import escape as html_escape
from typing import Any, Optional
from uuid import UUID

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT
from config import config
from course.disk_layout import lesson_sort_key
from course.disk_scan import scan_course_disk
from course.products import active_product, active_product_id, scoped_product_ids
from yandex_disk.webdav import YandexDiskWebDAV

logger = logging.getLogger(__name__)


class CourseDiskSyncFeature(BaseFeature):
    name = "course_disk_sync"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._pending_batch: dict[str, list] = {}

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.callback_query.register(
            self.cb_start_batch,
            CALLBACK_PRIVATE_CHAT,
            lambda c: (c.data or "").startswith("ds:go:"),
        )

    async def start_background_tasks(self) -> None:
        if not getattr(config, "COURSE_ENABLED", False):
            return
        if not config.YANDEX_DISK_LOGIN:
            self.log("нет YANDEX_DISK_LOGIN — опрос Диска не запущен", level="warning")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll_loop(), name="course_disk_poll")
        self.log(f"опрос Диска каждые {config.COURSE_DISK_POLL_SEC} с")

    async def stop_background_tasks(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _poll_loop(self) -> None:
        interval = max(60, int(config.COURSE_DISK_POLL_SEC or 900))
        await asyncio.sleep(15)
        while True:
            try:
                await self.run_sync(notify=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("course disk poll: %s", e)
            await asyncio.sleep(interval)

    async def cmd_sync(self, message: Message) -> None:
        await self.run_sync_for_user(message, user_id=message.from_user.id)

    async def run_sync_for_user(self, message: Message, *, user_id: int) -> None:
        if not await is_admin_or_super(self._app.user_storage, user_id):
            return
        if self._lock.locked():
            await message.answer("Синхронизация уже идёт.")
            return
        await message.answer("Синхронизирую Яндекс.Диск…")
        summary = await self.run_sync(notify=True, chat_id=message.chat.id)
        await message.answer(summary, parse_mode=ParseMode.HTML)

    async def run_sync(self, *, notify: bool, chat_id: Optional[int] = None) -> str:
        async with self._lock:
            return await self._sync_unlocked(notify=notify, chat_id=chat_id)

    async def _sync_unlocked(self, *, notify: bool, chat_id: Optional[int]) -> str:
        from course.worker import CourseWorker

        dav = YandexDiskWebDAV(config.YANDEX_DISK_LOGIN, config.YANDEX_DISK_PASSWORD)
        if not dav.configured:
            return "Яндекс.Диск не настроен (логин/пароль)."
        scan = await scan_course_disk(
            dav,
            disk_root=config.COURSE_DISK_ROOT,
            active_product_id=active_product_id(),
        )
        stor = self._app.user_storage
        new_n = changed_n = deleted_n = same_n = 0
        need_confirm: list = []
        media_new = 0
        seen_paths = set()
        for item in scan.items:
            rf, role = item.remote, item.role
            seen_paths.add(rf.path)
            existing = await stor.get_course_source_by_disk_path(rf.path)
            if existing:
                if (existing.get("disk_etag") or "") == (rf.etag or "") and existing.get("status") != "deleted":
                    same_n += 1
                    continue
                # changed etag → reprocess
                await stor.archive_cards_for_source(existing["id"])
                rs = getattr(self._app, "rag_stack", None)
                if rs is not None:
                    rs.materials.delete_by_source(str(existing["id"]))
                await stor.update_course_source(
                    existing["id"],
                    disk_etag=rf.etag or "",
                    status="new",
                    attempts=0,
                    error_message="",
                    kind=role.kind,
                    title=rf.name,
                )
                changed_n += 1
                continue
            lesson_id = None
            if role.lesson_key:
                a, b = lesson_sort_key(role.lesson_key)
                lesson_id = await stor.upsert_course_lesson(
                    product_id=role.product_id,
                    lesson_key=role.lesson_key,
                    lesson_no=b or a,
                    module_no=role.module_no,
                    title=role.lesson_title,
                    disk_path=None,
                )
            if role.needs_lesson_confirm:
                need_confirm.append(item)
            dur = None
            if role.kind in ("lesson_video", "practice", "broadcast", "other"):
                media_new += 1
            sid = await stor.insert_course_source(
                product_id=role.product_id,
                origin="disk",
                kind=role.kind,
                lesson_id=lesson_id,
                title=rf.name,
                disk_path=rf.path,
                disk_etag=rf.etag or "",
                recorded_on=role.recorded_on,
                platform=role.platform,
                duration_sec=dur,
                status="new" if not role.needs_lesson_confirm else "skipped",
                added_by=config.SUPER_ADMIN_ID or 0,
            )
            if sid:
                new_n += 1

        # deleted
        known = await stor.list_course_sources_by_product(
            scoped_product_ids(), statuses=None
        )
        for row in known:
            if row.get("origin") != "disk" or not row.get("disk_path"):
                continue
            if row["disk_path"] in seen_paths:
                continue
            if row.get("status") == "deleted":
                continue
            await stor.update_course_source(row["id"], status="deleted")
            await stor.archive_cards_for_source(row["id"])
            rs = getattr(self._app, "rag_stack", None)
            if rs is not None:
                rs.materials.delete_by_source(str(row["id"]))
            deleted_n += 1

        product = active_product()
        summary = (
            f"<b>Диск → {html_escape(product.name)}</b>\n"
            f"новых {new_n}, изменённых {changed_n}, без изменений {same_n}, "
            f"удалённых {deleted_n}, пропуск {scan.skipped}"
        )
        if scan.errors:
            summary += "\nошибки: " + "; ".join(html_escape(e)[:80] for e in scan.errors[:5])

        studio = self._app.feature_manager.get_optional("content_studio")
        mass = new_n > 10 or media_new > 0
        if mass and notify and chat_id:
            # оценка — упрощённо
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(text="▶️ Запустить", callback_data="ds:go:1")
                ]]
            )
            await self._app.bot.send_message(
                chat_id,
                summary + f"\nВидео/аудио новых: {media_new}. Запустить обработку?",
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        elif new_n or changed_n:
            worker = getattr(self._app, "course_worker", None)
            # worker picks them up itself
            pass

        await self._admin(summary)
        if need_confirm and studio:
            await studio.ask_lesson_for_files(need_confirm)
        return summary

    async def cb_start_batch(self, callback: CallbackQuery) -> None:
        await callback.answer("Очередь запущена")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    async def _admin(self, html: str) -> None:
        from bot.utils.rag_admin_context import rag_admin_chat_topic

        chat, topic = rag_admin_chat_topic()
        if not chat or not self._app:
            return
        kwargs = {"parse_mode": ParseMode.HTML}
        if topic:
            kwargs["message_thread_id"] = topic
        try:
            await self._app.bot.send_message(chat, html, **kwargs)
        except Exception as e:
            logger.warning("disk sync admin: %s", e)
