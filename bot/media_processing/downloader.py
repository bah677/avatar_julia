"""
Скачивание файлов из Telegram во временную директорию.

Сначала — Bot API (``get_file`` + ``download_file``). Для больших файлов
или при ошибке — опционально HTTP-сервис MTProto (``TG_MTPRO_DOWNLOADER_*``),
которому передаётся публичная ссылка на пост ``t.me/...``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Optional

import aiofiles
import httpx
from aiogram import Bot

from config import config

from .config.settings import TEMP_FILE_CONFIG

logger = logging.getLogger(__name__)

# Лимит Bot API на скачивание файла ботом (документация Telegram).
_BOT_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024


class FileDownloader:
    """Скачивает файлы из Telegram во временное хранилище"""

    def __init__(self):
        self.base_dir = TEMP_FILE_CONFIG["base_dir"]
        self._ensure_temp_dir()

    def _ensure_temp_dir(self) -> None:
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info("📁 Временная директория: %s", self.base_dir)

    @staticmethod
    def _get_file_extension(file_path: Optional[str]) -> str:
        if file_path and "." in file_path:
            return os.path.splitext(file_path)[1]
        return ".tmp"

    @staticmethod
    def _mtproto_downloader_configured() -> bool:
        u = (config.TG_MTPRO_DOWNLOADER_URL or "").strip()
        k = (config.TG_MTPRO_DOWNLOADER_API_KEY or "").strip()
        return bool(u and k)

    async def _download_via_mtproto_service(
        self,
        post_url: str,
        suffix: str,
    ) -> Optional[str]:
        base = (config.TG_MTPRO_DOWNLOADER_URL or "").strip().rstrip("/")
        key = (config.TG_MTPRO_DOWNLOADER_API_KEY or "").strip()
        if not base or not key:
            return None

        fetch_url = f"{base}/v1/fetch"
        timeout = httpx.Timeout(600.0, connect=60.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    fetch_url,
                    json={"post_url": post_url},
                    headers={"X-Api-Key": key},
                )
                if r.status_code >= 400:
                    logger.error(
                        "MTProto downloader HTTP %s: %s",
                        r.status_code,
                        (r.text or "")[:800],
                    )
                    return None
                data = r.json()
                if not data.get("ok"):
                    logger.error("MTProto downloader: ответ без ok: %s", data)
                    return None
                file_url = (data.get("file_url") or "").strip()
                if not file_url:
                    return None

                with tempfile.NamedTemporaryFile(
                    suffix=suffix or ".tmp",
                    dir=self.base_dir,
                    delete=False,
                ) as tmp:
                    out_path = tmp.name

                async with client.stream("GET", file_url) as resp:
                    if resp.status_code != 200:
                        logger.error(
                            "MTProto file GET %s: %s",
                            resp.status_code,
                            file_url[:200],
                        )
                        await asyncio.to_thread(os.unlink, out_path)
                        return None
                    async with aiofiles.open(out_path, "wb") as out_f:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                await out_f.write(chunk)

                logger.info("✅ Файл через MTProto-сервис: %s", out_path)
                return out_path
        except Exception as e:
            logger.error("❌ MTProto downloader: %s", e, exc_info=True)
            return None

    async def download_file(
        self,
        file_id: str,
        bot: Bot,
        *,
        file_size: Optional[int] = None,
        post_url: Optional[str] = None,
        filename_hint: Optional[str] = None,
    ) -> Optional[str]:
        """
        Скачивает файл по ``file_id`` во временную директорию.

        Args:
            file_id: Telegram ``file_id``
            bot: экземпляр бота для Bot API
            file_size: размер из апдейта (может быть None)
            post_url: ссылка ``https://t.me/.../msg_id`` для MTProto-сервиса
            filename_hint: имя файла для расширения временного файла
        """
        suffix = self._get_file_extension(filename_hint)
        post = (post_url or "").strip()
        mtp = self._mtproto_downloader_configured() and bool(post)
        sz = file_size if file_size is not None else None

        async def try_mtproto() -> Optional[str]:
            if not mtp:
                return None
            return await self._download_via_mtproto_service(post, suffix)

        # Сразу MTProto, если размер заведомо выше лимита Bot API.
        if sz is not None and sz > _BOT_DOWNLOAD_MAX_BYTES and mtp:
            path = await try_mtproto()
            if path:
                return path

        try:
            file = await bot.get_file(file_id)
            suffix = self._get_file_extension(file.file_path or filename_hint)
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                dir=self.base_dir,
                delete=False,
            ) as tmp_file:
                file_path = tmp_file.name
            await bot.download_file(file.file_path, file_path)
            logger.debug(
                "✅ Файл скачан (Bot API): %s (%s bytes)",
                file_path,
                getattr(file, "file_size", "?"),
            )
            return file_path
        except Exception as e:
            logger.warning(
                "Bot API скачивание не удалось (%s), пробуем fallback…",
                e,
            )

        path = await try_mtproto()
        if path:
            return path

        logger.error("❌ Ошибка скачивания файла %s (Bot API + MTProto)", file_id)
        return None

    async def cleanup_file(self, file_path: str) -> None:
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug("✅ Временный файл удален: %s", file_path)
        except Exception as e:
            logger.error("❌ Ошибка удаления файла %s: %s", file_path, e)

    async def cleanup_old_files(self, hours: int | None = None) -> None:
        if hours is None:
            hours = TEMP_FILE_CONFIG["cleanup_age_hours"]

        try:
            now = datetime.now()
            cutoff = now - timedelta(hours=hours)

            for filename in os.listdir(self.base_dir):
                file_path = os.path.join(self.base_dir, filename)
                if os.path.isfile(file_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if mtime < cutoff:
                        os.unlink(file_path)
                        logger.debug("✅ Удален старый файл: %s", filename)

            logger.info("🧹 Очистка временных файлов завершена")
        except Exception as e:
            logger.error("❌ Ошибка очистки временных файлов: %s", e)
