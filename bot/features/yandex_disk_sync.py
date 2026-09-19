"""Фоновый импорт материалов с Яндекс.Диска в RAG + команды админа."""

from __future__ import annotations

import asyncio
import logging
from html import escape as html_escape
from typing import Any, Optional

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.filters.private_only import PRIVATE_CHAT
from config import config
from yandex_disk.sync import YandexDiskSyncService

logger = logging.getLogger(__name__)


class YandexDiskSyncFeature(BaseFeature):
    name = "yandex_disk_sync"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._task: Optional[asyncio.Task] = None
        self._sync_lock = asyncio.Lock()

    def set_bot(self, app: Any) -> None:
        self._app = app

    def _service(self) -> Optional[YandexDiskSyncService]:
        if self._app is None:
            return None
        rs = getattr(self._app, "rag_stack", None)
        if rs is None:
            return None
        return YandexDiskSyncService.from_config(
            config,
            user_storage=self._app.user_storage,
            openai_client=self._app.openai_client,
            material_index=rs.materials,
            bot_app=self._app,
        )

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        if not config.YANDEX_DISK_ENABLED:
            self.log("YANDEX_DISK_ENABLED=0 — хендлеры не регистрируются")
            return
        dispatcher.message.register(
            self.cmd_ydisk_status, PRIVATE_CHAT, Command("ydisk_status")
        )
        dispatcher.message.register(
            self.cmd_ydisk_sync, PRIVATE_CHAT, Command("ydisk_sync")
        )

    async def start_background_tasks(self) -> None:
        if not config.YANDEX_DISK_ENABLED:
            return
        svc = self._service()
        if svc is None or not svc.enabled:
            self.log(
                "фоновый импорт не запущен (нет login/RAG/sources)",
                level="warning",
            )
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._poll_loop(), name="yandex_disk_poll"
        )
        self.log(
            f"фоновый импорт запущен, интервал {config.YANDEX_DISK_POLL_INTERVAL_SEC} с"
        )

    async def stop_background_tasks(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _poll_loop(self) -> None:
        interval = max(300, int(config.YANDEX_DISK_POLL_INTERVAL_SEC or 3600))
        while True:
            try:
                await self._run_sync(notify_admin=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("yandex_disk poll: %s", e)
            await asyncio.sleep(interval)

    async def _run_sync(self, *, notify_admin: bool) -> str:
        async with self._sync_lock:
            svc = self._service()
            if svc is None or not svc.enabled:
                return "Яндекс.Диск: сервис не настроен (логин, RAG, sources)."
            results = await svc.sync_all()
        lines = ["<b>Яндекс.Диск → RAG</b>"]
        if not results:
            lines.append("Нет источников или синхронизация выключена.")
        for r in results:
            lines.append(
                f"• <code>{html_escape(r.source_id)}</code>: "
                f"скан {r.scanned}, маска {r.matched}, "
                f"новых {r.indexed}, пропуск {r.skipped}, ошибок {r.errors}"
            )
            for msg in (r.messages or [])[:3]:
                lines.append(f"  ⚠ {html_escape(msg)}")
        text = "\n".join(lines)
        if notify_admin and self._app and config.SUPER_ADMIN_ID:
            try:
                await self._app.bot.send_message(
                    config.SUPER_ADMIN_ID,
                    text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning("ydisk notify admin: %s", e)
        return text

    async def cmd_ydisk_status(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return
        svc = self._service()
        stats = await self._app.user_storage.list_yandex_disk_indexed_stats()
        lines = [
            "<b>Статус Яндекс.Диск → RAG</b>",
            f"Включено: <code>{config.YANDEX_DISK_ENABLED}</code>",
            f"WebDAV login: <code>{'да' if (config.YANDEX_DISK_LOGIN or '').strip() else 'нет'}</code>",
            f"Источников: <code>{svc.sources_count if svc else 0}</code>",
            f"Интервал: <code>{config.YANDEX_DISK_POLL_INTERVAL_SEC}</code> с",
        ]
        if stats:
            lines.append("\n<b>Проиндексировано:</b>")
            for row in stats:
                lines.append(
                    f"• <code>{html_escape(str(row['source_id']))}</code>: "
                    f"{row['files_count']} файлов, {row['chunks_total']} чанков"
                )
        else:
            lines.append("\nВ БД пока нет записей (или не применена миграция 014).")
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_ydisk_sync(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await self._is_admin(uid):
            await message.answer("Команда только для администратора.")
            return
        await message.answer("Запускаю синхронизацию с Яндекс.Диском…")
        text = await self._run_sync(notify_admin=False)
        await message.answer(text, parse_mode=ParseMode.HTML)
