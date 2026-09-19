"""Проверка суперадмина / bot_admins с коротким кэшем."""

from __future__ import annotations

import time
from typing import Dict, Tuple

_CACHE: Dict[int, Tuple[bool, float]] = {}
_TTL_SEC = 45.0


def is_super_admin_user_id(telegram_user_id: int) -> bool:
    from config import config

    sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
    return bool(sid) and telegram_user_id == sid


async def is_bot_admin(user_storage, telegram_user_id: int) -> bool:
    now = time.monotonic()
    hit = _CACHE.get(telegram_user_id)
    if hit is not None:
        ok, ts = hit
        if now - ts < _TTL_SEC:
            return ok
    ok = False
    try:
        ok = bool(await user_storage.is_bot_admin(telegram_user_id))
    except Exception:
        ok = False
    _CACHE[telegram_user_id] = (ok, now)
    return ok


async def is_admin_or_super(user_storage, telegram_user_id: int) -> bool:
    if is_super_admin_user_id(telegram_user_id):
        return True
    return await is_bot_admin(user_storage, telegram_user_id)


def invalidate_admin_cache(telegram_user_id: int) -> None:
    _CACHE.pop(telegram_user_id, None)
