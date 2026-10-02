"""Вход в студию через Telegram: выбор админа → код от бота → сессия.

Список админов отдаётся без telegram id: у каждого непрозрачная ссылка `ref`
(HMAC от user_id на серверном секрете), поэтому по странице нельзя собрать id
и нельзя попросить код для чужого аккаунта.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

from bot.admin_guard import is_admin_or_super

logger = logging.getLogger(__name__)

CODE_TTL_SEC = 600
CODE_MAX_ATTEMPTS = 5
CODE_MAX_PER_WINDOW = 5
CODE_WINDOW_SEC = 900
SESSION_TTL_DAYS = 30

_SESSION_CACHE: Dict[str, tuple[int, float]] = {}
_SESSION_CACHE_TTL = 60.0


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class AdminRef:
    user_id: int
    ref: str
    name: str


def _secret() -> str:
    from config import config

    raw = str(getattr(config, "WEB_SECRET", "") or "").strip()
    if not raw:
        raw = str(getattr(config, "WEB_AUTH_TOKEN", "") or "").strip()
    if not raw:
        raise AuthError("WEB_SECRET не задан", status=503)
    return raw


def admin_ref(user_id: int) -> str:
    digest = hmac.new(_secret().encode("utf-8"), str(int(user_id)).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:20]


def hash_token(token: str) -> str:
    return hmac.new(_secret().encode("utf-8"), (token or "").encode("utf-8"), hashlib.sha256).hexdigest()


def hash_code(user_id: int, code: str) -> str:
    payload = f"{int(user_id)}:{(code or '').strip()}"
    return hmac.new(_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def display_name(user: Optional[Dict[str, Any]], user_id: int) -> str:
    row = user or {}
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip().lstrip("@").strip()
    username = str(row.get("username") or "").strip().lstrip("@")
    if first:
        return (f"{first} {last[:1]}." if last else first).strip()
    if last:
        return last
    if username:
        return f"@{username}"
    return f"админ {str(user_id)[-4:]}"


async def _name_from_telegram(bot_app, user_id: int) -> Optional[Dict[str, Any]]:
    """Имя для списка входа, если в users его ещё нет: спрашиваем у Telegram."""
    bot = getattr(bot_app, "bot", None)
    if bot is None:
        return None
    try:
        chat = await bot.get_chat(user_id)
    except Exception as e:
        logger.debug("get_chat %s: %s", user_id, e)
        return None
    data = {
        "user_id": user_id,
        "username": getattr(chat, "username", None),
        "first_name": getattr(chat, "first_name", None),
        "last_name": getattr(chat, "last_name", None),
    }
    if not (data["first_name"] or data["last_name"] or data["username"]):
        return None
    try:
        await bot_app.user_storage.add_or_update_user(data)
    except Exception as e:
        logger.debug("add_or_update_user %s: %s", user_id, e)
    return data


async def admin_list(bot_app) -> List[AdminRef]:
    """Суперадмин + bot_admins, без telegram id в выдаче."""
    from config import config

    stor = bot_app.user_storage if hasattr(bot_app, "user_storage") else bot_app
    ids: List[int] = []
    sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
    if sid:
        ids.append(sid)
    for uid in await stor.list_bot_admin_ids():
        if int(uid) not in ids:
            ids.append(int(uid))
    out: List[AdminRef] = []
    for uid in ids:
        user = None
        try:
            user = await stor.get_user(uid)
        except Exception as e:  # pragma: no cover
            logger.debug("get_user %s: %s", uid, e)
        has_name = any(
            str((user or {}).get(k) or "").strip() for k in ("first_name", "last_name", "username")
        )
        if not has_name and hasattr(bot_app, "bot"):
            user = await _name_from_telegram(bot_app, uid) or user
        out.append(AdminRef(user_id=uid, ref=admin_ref(uid), name=display_name(user, uid)))
    return out


async def resolve_ref(bot_app, ref: str) -> int:
    clean = (ref or "").strip()
    for item in await admin_list(bot_app):
        if secrets.compare_digest(item.ref, clean):
            return item.user_id
    raise AuthError("Такого администратора нет", status=404)


async def request_code(bot_app, ref: str) -> Dict[str, Any]:
    """Генерирует код, отправляет его админу в Telegram."""
    stor = bot_app.user_storage
    user_id = await resolve_ref(bot_app, ref)
    recent = await stor.count_recent_web_login_codes(user_id=user_id, window_sec=CODE_WINDOW_SEC)
    if recent >= CODE_MAX_PER_WINDOW:
        raise AuthError("Слишком много попыток входа. Подождите 15 минут.", status=429)

    code = str(secrets.randbelow(900_000) + 100_000)
    code_id = await stor.insert_web_login_code(
        user_id=user_id, code_hash=hash_code(user_id, code), ttl_sec=CODE_TTL_SEC
    )
    if not code_id:
        raise AuthError("Не удалось создать код", status=500)

    text = (
        "🔑 <b>Код для входа в контент-студию</b>\n\n"
        f"<code>{code}</code>\n\n"
        f"Действует {CODE_TTL_SEC // 60} минут. "
        "Если вы не запрашивали вход — просто не вводите код и скажите разработчику."
    )
    try:
        await bot_app.bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as e:
        logger.warning("web login code send to %s: %s", user_id, e)
        raise AuthError(
            "Не получилось отправить код в Telegram. Напишите боту /start и попробуйте снова.",
            status=502,
        ) from e
    return {"ok": True, "ttl_sec": CODE_TTL_SEC}


async def verify_code(bot_app, *, ref: str, code: str, user_agent: str = "", ip: str = "") -> Dict[str, Any]:
    """Проверяет код и выдаёт токен сессии."""
    stor = bot_app.user_storage
    user_id = await resolve_ref(bot_app, ref)
    row = await stor.get_active_web_login_code(user_id=user_id)
    if not row:
        raise AuthError("Код истёк. Запросите новый.", status=410)
    if int(row.get("attempts") or 0) >= CODE_MAX_ATTEMPTS:
        raise AuthError("Слишком много неверных попыток. Запросите новый код.", status=429)
    expected = str(row.get("code_hash") or "")
    given = hash_code(user_id, (code or "").strip())
    if not expected or not secrets.compare_digest(expected, given):
        await stor.bump_web_login_attempts(int(row["id"]))
        raise AuthError("Неверный код", status=401)
    if not await is_admin_or_super(stor, user_id):
        raise AuthError("Доступ только для администраторов проекта", status=403)

    await stor.mark_web_login_code_used(int(row["id"]))
    token = secrets.token_urlsafe(32)
    await stor.insert_web_session(
        token_hash=hash_token(token),
        user_id=user_id,
        user_agent=user_agent,
        ip=ip,
        ttl_days=SESSION_TTL_DAYS,
    )
    user = None
    try:
        user = await stor.get_user(user_id)
    except Exception:
        pass
    return {
        "token": token,
        "user": {"name": display_name(user, user_id), "ref": admin_ref(user_id)},
        "ttl_days": SESSION_TTL_DAYS,
    }


async def session_user_id(stor, token: Optional[str]) -> int:
    """user_id по cookie сессии; 0 — если сессии нет или админа лишили прав."""
    raw = (token or "").strip()
    if not raw:
        return 0
    key = hash_token(raw)
    hit = _SESSION_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[1] < _SESSION_CACHE_TTL:
        return hit[0]
    row = await stor.get_web_session(key)
    if not row:
        _SESSION_CACHE.pop(key, None)
        return 0
    uid = int(row.get("user_id") or 0)
    if not uid or not await is_admin_or_super(stor, uid):
        await stor.delete_web_session(key)
        _SESSION_CACHE.pop(key, None)
        return 0
    _SESSION_CACHE[key] = (uid, now)
    try:
        await stor.touch_web_session(key)
    except Exception:
        pass
    return uid


async def drop_session(stor, token: Optional[str]) -> None:
    raw = (token or "").strip()
    if not raw:
        return
    key = hash_token(raw)
    _SESSION_CACHE.pop(key, None)
    await stor.delete_web_session(key)


# ── Вход из мини-аппа Telegram ──────────────────────────────────────────────

INIT_DATA_MAX_AGE_SEC = 24 * 3600


def parse_init_data(init_data: str) -> Dict[str, Any]:
    """Проверяет подпись `initData` мини-аппа и возвращает его поля.

    Алгоритм Telegram: secret = HMAC-SHA256(ключ "WebAppData", токен бота),
    затем hash = HMAC-SHA256(secret, строка из отсортированных пар key=value).
    """
    from config import config

    raw = (init_data or "").strip()
    if not raw:
        raise AuthError("Пустой initData", status=400)
    pairs = dict(parse_qsl(raw, keep_blank_values=True))
    received = str(pairs.pop("hash", ""))
    if not received:
        raise AuthError("В initData нет подписи", status=400)

    token = str(getattr(config, "BIBLIA_BOT_TOKEN", "") or "").strip()
    if not token:
        raise AuthError("Не задан токен бота", status=503)

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("Подпись Telegram не сошлась", status=401)

    try:
        auth_date = int(pairs.get("auth_date") or 0)
    except ValueError:
        auth_date = 0
    if not auth_date or time.time() - auth_date > INIT_DATA_MAX_AGE_SEC:
        raise AuthError("Данные входа устарели, откройте студию заново", status=401)

    user: Dict[str, Any] = {}
    if pairs.get("user"):
        try:
            user = json.loads(pairs["user"])
        except json.JSONDecodeError:
            user = {}
    if not user.get("id"):
        raise AuthError("В initData нет пользователя", status=400)
    return {"user": user, "auth_date": auth_date}


async def login_with_init_data(
    bot_app, *, init_data: str, user_agent: str = "", ip: str = ""
) -> Dict[str, Any]:
    """Мини-апп: подпись верна и пользователь — админ → сессия."""
    stor = bot_app.user_storage
    data = parse_init_data(init_data)
    tg_user = data["user"]
    user_id = int(tg_user["id"])

    if not await is_admin_or_super(stor, user_id):
        raise AuthError("Студия доступна только администраторам проекта", status=403)

    # Профиль из Telegram свежее того, что в базе.
    try:
        await stor.add_or_update_user(
            {
                "user_id": user_id,
                "username": tg_user.get("username"),
                "first_name": tg_user.get("first_name"),
                "last_name": tg_user.get("last_name"),
                "language_code": tg_user.get("language_code"),
                "is_premium": bool(tg_user.get("is_premium")),
            }
        )
    except Exception as e:
        logger.debug("add_or_update_user from initData: %s", e)

    token = secrets.token_urlsafe(32)
    await stor.insert_web_session(
        token_hash=hash_token(token),
        user_id=user_id,
        user_agent=user_agent or "telegram-mini-app",
        ip=ip,
        ttl_days=SESSION_TTL_DAYS,
    )
    name = display_name(
        {
            "first_name": tg_user.get("first_name"),
            "last_name": tg_user.get("last_name"),
            "username": tg_user.get("username"),
        },
        user_id,
    )
    logger.info("web studio: вход из мини-аппа, user_id=%s", user_id)
    return {"token": token, "user": {"name": name, "ref": admin_ref(user_id)}, "ttl_days": SESSION_TTL_DAYS}
