"""Ожидание и скачивание аудио-записи эфира."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from config import config
from telemost_mail.imap_client import YandexImapClient
from telemost_mail.recording_parse import _url_video_score, parse_recording_email
from telemost_mail.recording_resolver import scan_imap_for_recording
from telemost_mail.video_download import download_recording_video
from telemost_mail.webdav_recording import download_telemost_audio_webdav

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Any]


async def _notify(notify: Optional[NotifyFn], text: str) -> None:
    if notify is None:
        return
    try:
        result = notify(text)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        logger.warning("audio_resolver notify: %s", e)


async def wait_and_download_audio(
    meeting_id: str,
    *,
    storage,
    imap: YandexImapClient,
    notify: Optional[NotifyFn] = None,
) -> Optional[str]:
    mid = (meeting_id or "").strip()
    if not mid:
        return None

    wait_sec = int(getattr(config, "TELEMOST_SHORTS_WAIT_RECORDING_SEC", 7200) or 7200)
    poll_sec = int(getattr(config, "TELEMOST_SHORTS_POLL_INTERVAL_SEC", 120) or 120)
    dest = getattr(config, "TELEMOST_AUDIO_DIR", "data/telemost_audio")

    async def _try_download(url: str) -> Optional[str]:
        login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
        password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
        if login and password:
            await _notify(notify, "⬇️ Скачиваю аудио с Я.Диска…")
            path = await download_telemost_audio_webdav(mid, dest_dir=dest)
            if path:
                return path
        if url:
            path = await asyncio.to_thread(
                download_recording_video,
                url,
                f"{mid}_audio",
                dest_dir=dest,
            )
            if path:
                return path
        if login and password:
            return await download_telemost_audio_webdav(mid, dest_dir=dest)
        return None

    deadline = time.monotonic() + max(60, wait_sec)
    notified_wait = False

    while time.monotonic() < deadline:
        row = await storage.get_telemost_recording(mid)
        if row:
            local = (row.get("local_audio_path") or "").strip()
            if local:
                from pathlib import Path

                if Path(local).is_file():
                    return local
            url = (row.get("audio_url") or "").strip()
            if url and _url_video_score(url) > 0:
                await _notify(notify, "⬇️ Скачиваю аудио-запись…")
                path = await _try_download(url)
                if path:
                    await storage.set_telemost_recording_local_audio_path(mid, path)
                    return path

        await scan_imap_for_recording(imap, mid, storage=storage, limit=80)
        row = await storage.get_telemost_recording(mid)
        if row:
            local = (row.get("local_audio_path") or "").strip()
            if local:
                from pathlib import Path

                if Path(local).is_file():
                    return local
            url = (row.get("audio_url") or "").strip()
            if url:
                path = await _try_download(url)
                if path:
                    await storage.set_telemost_recording_local_audio_path(mid, path)
                    return path
            path = await download_telemost_audio_webdav(mid, dest_dir=dest)
            if path:
                await storage.set_telemost_recording_local_audio_path(mid, path)
                return path

        if not row and not notified_wait:
            await _notify(
                notify,
                f"⏳ Жду письмо с <b>записью</b> (аудио) №<code>{mid}</code>…",
            )
            notified_wait = True

        await asyncio.sleep(max(30, poll_sec))

    return None
