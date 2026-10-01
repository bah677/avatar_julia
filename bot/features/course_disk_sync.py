"""Фоновый опрос Яндекс.Диска и команда /sync."""

from __future__ import annotations

import asyncio
import logging
from html import escape as html_escape
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT
from config import config
from course.disk_layout import DISK_TRANSCRIBE_KINDS, MEDIA_EXTS, lesson_sort_key
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
        from course.disk_identity import content_changed, file_fingerprint

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
        present_paths = set(scan.listed_paths)
        for item in scan.items:
            rf, role = item.remote, item.role
            if role.link_list:
                present_paths.discard(rf.path)
                n, c, s, media = await self._sync_video_link_file(
                    dav, stor, item, present_paths
                )
                new_n += n
                changed_n += c
                same_n += s
                media_new += media
                continue
            present_paths.add(rf.path)
            existing = await stor.get_course_source_by_disk_path(rf.path)
            if existing:
                fp = file_fingerprint(size=rf.size, modified=rf.modified, etag=rf.etag)
                real_change = content_changed(
                    existing.get("disk_etag") or "",
                    size=rf.size,
                    modified=rf.modified,
                    etag=rf.etag,
                )
                if not real_change:
                    fields: dict = {}
                    if (existing.get("disk_etag") or "") != fp:
                        fields["disk_etag"] = fp
                    if existing.get("status") == "deleted":
                        fields["status"] = (
                            "done"
                            if existing.get("processed_at") or existing.get("chars_count")
                            else "new"
                        )
                        fields["error_message"] = ""
                    if fields:
                        await stor.update_course_source(existing["id"], **fields)
                    same_n += 1
                    continue
                await stor.archive_cards_for_source(existing["id"])
                rs = getattr(self._app, "rag_stack", None)
                if rs is not None:
                    rs.materials.delete_by_source(str(existing["id"]))
                await stor.update_course_source(
                    existing["id"],
                    disk_etag=fp,
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
            if role.kind in DISK_TRANSCRIBE_KINDS and Path(rf.name).suffix.lower() in MEDIA_EXTS:
                media_new += 1
            sid = await stor.insert_course_source(
                product_id=role.product_id,
                origin="disk",
                kind=role.kind,
                lesson_id=lesson_id,
                module_no=role.module_no,
                title=rf.name,
                disk_path=rf.path,
                disk_etag=file_fingerprint(
                    size=rf.size, modified=rf.modified, etag=rf.etag
                ),
                recorded_on=role.recorded_on,
                platform=role.platform,
                duration_sec=dur,
                status="new" if not role.needs_lesson_confirm else "skipped",
                added_by=config.SUPER_ADMIN_ID or 0,
            )
            if sid:
                new_n += 1

        # deleted
        if scan.errors:
            logger.warning(
                "disk scan incomplete, skip deletions: %s", "; ".join(scan.errors[:5])
            )
        known = await stor.list_course_sources_by_product(
            scoped_product_ids(), statuses=None
        )
        for row in known:
            if scan.errors:
                break
            origin = row.get("origin") or ""
            dp = row.get("disk_path") or ""
            if not dp:
                continue
            is_link_child = "::" in dp
            if origin != "disk" and not is_link_child:
                continue
            if dp in present_paths:
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

    async def _sync_video_link_file(
        self,
        dav: YandexDiskWebDAV,
        stor: Any,
        item: Any,
        present_paths: set,
    ) -> tuple[int, int, int, int]:
        from course.disk_identity import content_changed, file_fingerprint
        from course.video_hosts import adapter_for
        from course.video_links import link_disk_key, parse_video_link_file

        rf, role = item.remote, item.role
        new_n = changed_n = same_n = media_n = 0
        try:
            text = await dav.get_text(rf.path)
        except Exception as e:
            logger.warning("link file read %s: %s", rf.path, e)
            return 0, 0, 0, 0
        entries = parse_video_link_file(text)
        fp = file_fingerprint(size=rf.size, modified=rf.modified, etag=rf.etag)
        for i, entry in enumerate(entries):
            child = link_disk_key(rf.path, i)
            present_paths.add(child)
            lesson_key = entry.lesson_key or role.lesson_key
            module_no = (
                entry.module_no if entry.module_no is not None else role.module_no
            )
            kind = entry.kind or role.kind or "other"
            lesson_id = None
            if lesson_key:
                a, b = lesson_sort_key(lesson_key)
                lesson_id = await stor.upsert_course_lesson(
                    product_id=role.product_id,
                    lesson_key=lesson_key,
                    lesson_no=b or a,
                    module_no=a if b else module_no,
                    title=role.lesson_title,
                    disk_path=None,
                )
                if module_no is None and b:
                    module_no = a
            adapter = adapter_for(entry.host, video_password=entry.password)
            probe = None
            try:
                probe = await adapter.probe(entry.url)
            except Exception as e:
                logger.warning("link probe %s: %s", entry.url, e)
            video_id = (probe.video_id if probe else "") or ""
            existing = await stor.get_course_source_by_disk_path(child)
            if not existing and video_id:
                by_vid = await stor.get_course_source_by_video(entry.host, video_id)
                if by_vid:
                    existing = by_vid
            meta = dict(existing.get("metadata") or {}) if existing else {}
            if entry.password:
                meta["zoom_password"] = entry.password
            if module_no and not lesson_key:
                meta["module_only"] = True
            if entry.description:
                meta["description"] = entry.description[:4000]
            elif "description" in meta:
                meta.pop("description", None)
            title = (probe.title if probe else "") or entry.url
            duration = probe.duration_sec if probe else None
            recorded = role.recorded_on or (probe.recorded_on if probe else None)
            if existing:
                url_changed = (existing.get("url") or "") != entry.url
                old_desc = str((existing.get("metadata") or {}).get("description") or "")
                desc_changed = old_desc != (entry.description or "")
                real_change = content_changed(
                    existing.get("disk_etag") or "",
                    size=rf.size,
                    modified=rf.modified,
                    etag=rf.etag,
                )
                if not real_change and not url_changed and not desc_changed:
                    fields: dict = {}
                    if (existing.get("disk_etag") or "") != fp:
                        fields["disk_etag"] = fp
                    if existing.get("disk_path") != child:
                        fields["disk_path"] = child
                    if existing.get("status") == "deleted":
                        fields["status"] = (
                            "done"
                            if existing.get("processed_at") or existing.get("chars_count")
                            else "new"
                        )
                        fields["error_message"] = ""
                    if fields:
                        await stor.update_course_source(existing["id"], **fields)
                    same_n += 1
                    continue
                await stor.archive_cards_for_source(existing["id"])
                rs = getattr(self._app, "rag_stack", None)
                if rs is not None:
                    rs.materials.delete_by_source(str(existing["id"]))
                await stor.update_course_source(
                    existing["id"],
                    origin=entry.host,
                    kind=kind,
                    lesson_id=lesson_id,
                    module_no=module_no,
                    title=title,
                    url=entry.url,
                    video_id=video_id or None,
                    duration_sec=duration,
                    recorded_on=recorded,
                    disk_path=child,
                    disk_etag=fp,
                    metadata=meta,
                    status="new",
                    attempts=0,
                    error_message="",
                )
                changed_n += 1
                media_n += 1
                continue
            sid = await stor.insert_course_source(
                product_id=role.product_id,
                origin=entry.host,
                kind=kind,
                lesson_id=lesson_id,
                module_no=module_no,
                title=title,
                url=entry.url,
                video_id=video_id or None,
                duration_sec=duration,
                recorded_on=recorded,
                disk_path=child,
                disk_etag=fp,
                status="new",
                added_by=config.SUPER_ADMIN_ID or 0,
                metadata=meta,
            )
            if sid:
                new_n += 1
                media_n += 1
        return new_n, changed_n, same_n, media_n

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
