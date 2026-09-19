"""Записи Телемоста на Я.Диске пользователя (WebDAV), если yadi.sk недоступен."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Sequence

from config import config
from yandex_disk.webdav import RemoteFile, YandexDiskWebDAV

logger = logging.getLogger(__name__)

_VIDEO_EXTS = (".webm", ".mp4", ".mkv", ".mov")


def _safe_meeting_id(meeting_id: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (meeting_id or "unknown").strip())[:64]


def _webdav_dirs() -> Sequence[str]:
    custom = (getattr(config, "TELEMOST_RECORDINGS_WEBDAV_DIR", "") or "").strip()
    dirs = []
    if custom:
        dirs.append(custom)
    for d in ("/Записи Телемоста", "Записи Телемоста"):
        if d not in dirs:
            dirs.append(d)
    return dirs


def _is_video_recording_file(remote: RemoteFile, meeting_id: str) -> bool:
    name = (remote.name or "").lower()
    path = (remote.path or "").lower()
    if meeting_id not in name and meeting_id not in path:
        return False
    if "audio_only" in name:
        return False
    return any(name.endswith(ext) for ext in _VIDEO_EXTS)


async def find_telemost_video_on_webdav(meeting_id: str) -> Optional[str]:
    """Путь на WebDAV к видеозаписи встречи или None."""
    mid = (meeting_id or "").strip()
    if not mid:
        return None
    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    best: Optional[RemoteFile] = None
    for dir_path in _webdav_dirs():
        try:
            files = await webdav.list_files(dir_path, recursive=True)
        except Exception as e:
            logger.warning("webdav list %s: %s", dir_path, e)
            continue
        for remote in files:
            if not _is_video_recording_file(remote, mid):
                continue
            if best is None or remote.size > best.size:
                best = remote
        if best is not None:
            break

    if best is None:
        return None
    logger.info(
        "webdav recording hit meeting_id=%s path=%s size=%s",
        mid,
        best.path,
        best.size,
    )
    return best.path


_AUDIO_EXTS = (".mp3", ".m4a", ".ogg", ".opus", ".wav")


def _is_audio_recording_file(remote: RemoteFile, meeting_id: str) -> bool:
    name = (remote.name or "").lower()
    path = (remote.path or "").lower()
    if meeting_id not in name and meeting_id not in path:
        return False
    return "audio" in name or any(name.endswith(ext) for ext in _AUDIO_EXTS)


async def find_telemost_audio_on_webdav(meeting_id: str) -> Optional[str]:
    mid = (meeting_id or "").strip()
    if not mid:
        return None
    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    best: Optional[RemoteFile] = None
    for dir_path in _webdav_dirs():
        try:
            files = await webdav.list_files(dir_path, recursive=True)
        except Exception as e:
            logger.warning("webdav list %s: %s", dir_path, e)
            continue
        for remote in files:
            if not _is_audio_recording_file(remote, mid):
                continue
            if best is None or remote.size > best.size:
                best = remote
        if best is not None:
            break

    if best is None:
        return None
    logger.info(
        "webdav audio hit meeting_id=%s path=%s size=%s",
        mid,
        best.path,
        best.size,
    )
    return best.path


async def download_telemost_audio_webdav(
    meeting_id: str,
    *,
    dest_dir: str | Path,
) -> Optional[str]:
    remote_path = await find_telemost_audio_on_webdav(meeting_id)
    if not remote_path:
        return None

    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    root = Path(dest_dir)
    root.mkdir(parents=True, exist_ok=True)
    ext = Path(remote_path).suffix.lower() or ".mp3"
    out = root / f"{_safe_meeting_id(meeting_id)}_audio{ext}"
    if out.is_file() and out.stat().st_size > 10_000:
        return str(out.resolve())

    try:
        await webdav.download(remote_path, str(out))
    except Exception as e:
        logger.error("webdav audio download meeting_id=%s: %s", meeting_id, e)
        return None

    if out.is_file() and out.stat().st_size > 10_000:
        logger.info("audio downloaded via webdav: %s", out)
        return str(out.resolve())
    return None


async def download_telemost_recording_webdav(
    meeting_id: str,
    *,
    dest_dir: str | Path,
) -> Optional[str]:
    """Скачивает запись с Я.Диска по meeting_id в каталог dest_dir."""
    remote_path = await find_telemost_video_on_webdav(meeting_id)
    if not remote_path:
        return None

    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    root = Path(dest_dir)
    root.mkdir(parents=True, exist_ok=True)
    ext = Path(remote_path).suffix.lower() or ".webm"
    out = root / f"{_safe_meeting_id(meeting_id)}{ext}"
    if out.is_file() and out.stat().st_size > 10_000:
        return str(out.resolve())

    try:
        await webdav.download(remote_path, str(out))
    except Exception as e:
        logger.error("webdav download meeting_id=%s: %s", meeting_id, e)
        return None

    if out.is_file() and out.stat().st_size > 10_000:
        logger.info("recording downloaded via webdav: %s", out)
        return str(out.resolve())
    return None
