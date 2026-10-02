"""HTTP API веб-студии: дерево объектов, чаты, ход генерации."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import Body, Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from course.formats import FORMATS
from course.paths import source_dir
from course.products import active_product_id, product_display_name
from course.stories_cycle import STAGES, normalize_stage
from web.auth import (
    AuthError,
    admin_list,
    display_name,
    drop_session,
    hash_token,
    login_with_init_data,
    request_code,
    session_user_id,
    verify_code,
)
from web.objects import build_tree, object_names, parse_object_id, parse_object_ids, source_label
from web.pipeline import raw_text_for_source, run_turn

logger = logging.getLogger(__name__)

COOKIE_NAME = "avatar_studio"
STATIC_DIR = Path(__file__).resolve().parent / "static"

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_TTL_SEC = 1800

_STAGE_TITLES = {
    "plan": "Планирую, что искать",
    "raw": "Читаю материалы",
    "distill": "Читаю сырьё и выбираю нужное",
    "search": "Ищу в базе знаний",
    "write": "Пишу",
}


def _cleanup_jobs() -> None:
    now = time.time()
    for key in [k for k, v in _JOBS.items() if now - float(v.get("started") or now) > _JOB_TTL_SEC]:
        _JOBS.pop(key, None)


def create_app(bot_app) -> FastAPI:
    from config import config

    app = FastAPI(title="Контент-студия", docs_url=None, redoc_url=None, openapi_url=None)

    def _stor():
        return bot_app.user_storage

    async def auth(
        request: Request,
        studio: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    ) -> int:
        """Возвращает telegram user_id вошедшего админа (cookie или Bearer)."""
        token = studio
        if not token:
            header = request.headers.get("authorization") or ""
            if header.lower().startswith("bearer "):
                token = header[7:].strip()
        try:
            uid = await session_user_id(_stor(), token)
        except AuthError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        if not uid:
            raise HTTPException(status_code=401, detail="Нужен вход")
        return uid

    def _client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for") or ""
        if fwd:
            return fwd.split(",")[0].strip()[:64]
        return (request.client.host if request.client else "")[:64]

    def _set_session_cookie(request: Request, response: Response, token: str, days: int) -> None:
        # secure=True только когда запрос реально пришёл по https: иначе cookie
        # не сохранится при проверке через ssh-туннель на http://localhost.
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=60 * 60 * 24 * int(days),
            httponly=True,
            samesite="lax",
            secure=proto == "https",
        )

    # ── Вход через Telegram ────────────────────────────────────────────────
    @app.get("/api/admins")
    async def admins():
        """Кто может войти: имена без telegram id, у каждого непрозрачная ссылка."""
        try:
            items = await admin_list(bot_app)
        except AuthError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        return {
            "admins": [{"ref": a.ref, "name": a.name} for a in items],
            "token_login": bool(getattr(config, "WEB_TOKEN_LOGIN", False)),
        }

    @app.post("/api/login/request")
    async def login_request(payload: Dict[str, Any] = Body(default={})):
        try:
            return await request_code(bot_app, str((payload or {}).get("ref") or ""))
        except AuthError as e:
            raise HTTPException(status_code=e.status, detail=e.message)

    @app.post("/api/login/verify")
    async def login_verify(
        request: Request, response: Response, payload: Dict[str, Any] = Body(default={})
    ):
        try:
            data = await verify_code(
                bot_app,
                ref=str((payload or {}).get("ref") or ""),
                code=str((payload or {}).get("code") or ""),
                user_agent=request.headers.get("user-agent", ""),
                ip=_client_ip(request),
            )
        except AuthError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        _set_session_cookie(request, response, data["token"], data["ttl_days"])
        return {"ok": True, "user": data["user"]}

    @app.post("/api/login/telegram")
    async def login_telegram(
        request: Request, response: Response, payload: Dict[str, Any] = Body(default={})
    ):
        """Мини-апп Telegram: проверяем подпись initData и пускаем только админов."""
        try:
            data = await login_with_init_data(
                bot_app,
                init_data=str((payload or {}).get("init_data") or ""),
                user_agent=request.headers.get("user-agent", ""),
                ip=_client_ip(request),
            )
        except AuthError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        _set_session_cookie(request, response, data["token"], data["ttl_days"])
        # Токен отдаём и телом: внутри Telegram cookie иногда не сохраняется.
        return {"ok": True, "user": data["user"], "token": data["token"]}

    @app.post("/api/login/token")
    async def login_token(
        request: Request, response: Response, payload: Dict[str, Any] = Body(default={})
    ):
        """Аварийный вход по общему коду: только при WEB_TOKEN_LOGIN=1."""
        if not bool(getattr(config, "WEB_TOKEN_LOGIN", False)):
            raise HTTPException(status_code=404, detail="Вход по общему коду отключён")
        expected = str(getattr(config, "WEB_AUTH_TOKEN", "") or "")
        given = str((payload or {}).get("token") or "")
        if not expected or not given or not secrets.compare_digest(given, expected):
            raise HTTPException(status_code=401, detail="Неверный код доступа")
        uid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
        if not uid:
            raise HTTPException(status_code=503, detail="SUPER_ADMIN_ID не задан")
        token = secrets.token_urlsafe(32)
        await _stor().insert_web_session(
            token_hash=hash_token(token),
            user_id=uid,
            user_agent=request.headers.get("user-agent", ""),
            ip=_client_ip(request),
            ttl_days=7,
        )
        _set_session_cookie(request, response, token, 7)
        return {"ok": True, "user": {"name": "аварийный вход", "ref": ""}}

    @app.get("/api/me")
    async def me(uid: int = Depends(auth)):
        user = None
        try:
            user = await _stor().get_user(uid)
        except Exception:
            pass
        return {"name": display_name(user, uid)}

    @app.post("/api/logout")
    async def logout(
        response: Response, studio: Optional[str] = Cookie(default=None, alias=COOKIE_NAME)
    ):
        try:
            await drop_session(_stor(), studio)
        except AuthError:
            pass
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    # ── Данные для интерфейса ──────────────────────────────────────────────
    @app.get("/api/bootstrap")
    async def bootstrap(uid: int = Depends(auth)):
        pid = active_product_id()
        tree = await build_tree(bot_app)
        chats = await _stor().list_web_chats(pid)
        focus = await _stor().get_content_setting(pid, "focus")
        stage = await _stor().get_content_setting(pid, "stories_cycle_stage")
        return {
            "product": {"id": pid, "name": product_display_name(pid)},
            "formats": [
                {"id": f.id, "title": f.title, "platform": f.platform} for f in FORMATS.values()
            ],
            "stages": [{"id": s[0], "title": s[1], "hint": s[2]} for s in STAGES],
            "defaults": {
                "focus": str(focus or "") if isinstance(focus, str) else "",
                "stage": normalize_stage(stage if isinstance(stage, str) else ""),
            },
            "tree": tree,
            "chats": [_chat_brief(c) for c in chats],
        }

    @app.get("/api/tree")
    async def tree(uid: int = Depends(auth)):
        return await build_tree(bot_app)

    @app.get("/api/sources/{source_id}")
    async def source_details(source_id: str, uid: int = Depends(auth)):
        try:
            sid = UUID(source_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="плохой id")
        src = await _stor().get_course_source(sid)
        if not src:
            raise HTTPException(status_code=404, detail="источник не найден")
        raw = raw_text_for_source(source_id)
        cards = await _stor().count_cards_for_source(sid)
        return {
            "id": source_id,
            "name": source_label(src),
            "kind": src.get("kind"),
            "origin": src.get("origin"),
            "status": src.get("status"),
            "url": src.get("url") or "",
            "disk_path": src.get("disk_path") or "",
            "duration_sec": src.get("duration_sec"),
            "recorded_on": str(src.get("recorded_on") or ""),
            "chars": len(raw),
            "cards": cards,
            "has_transcript": (source_dir(source_id) / "transcript.json").is_file(),
            "preview": raw[:4000],
        }

    @app.get("/api/queue")
    async def queue(uid: int = Depends(auth)):
        pid = active_product_id()
        rows = await _stor().list_course_queue([pid, "_expert"])
        return {
            "items": [
                {
                    "id": str(r.get("id")),
                    "name": source_label(r),
                    "status": r.get("status"),
                    "error": (r.get("error_message") or "")[:300],
                    "attempts": r.get("attempts"),
                }
                for r in rows
            ]
        }

    @app.post("/api/sync")
    async def sync(uid: int = Depends(auth)):
        feature = bot_app.feature_manager.get_optional("course_disk_sync")
        if feature is None or not hasattr(feature, "run_sync"):
            raise HTTPException(status_code=503, detail="синхронизация недоступна")
        asyncio.create_task(feature.run_sync(notify=False))
        return {"ok": True}

    # ── Чаты ───────────────────────────────────────────────────────────────
    @app.get("/api/chats")
    async def list_chats(uid: int = Depends(auth)):
        rows = await _stor().list_web_chats(active_product_id())
        return {"chats": [_chat_brief(c) for c in rows]}

    @app.post("/api/chats")
    async def create_chat(payload: Dict[str, Any] = Body(default={}), uid: int = Depends(auth)):
        pid = active_product_id()
        focus = payload.get("focus")
        stage = payload.get("stage")
        if focus is None:
            raw = await _stor().get_content_setting(pid, "focus")
            focus = str(raw or "") if isinstance(raw, str) else ""
        if stage is None:
            raw = await _stor().get_content_setting(pid, "stories_cycle_stage")
            stage = raw if isinstance(raw, str) else ""
        objects = [r.id for r in parse_object_ids(payload.get("objects") or [])]
        cid = await _stor().create_web_chat(
            product_id=pid,
            title=str(payload.get("title") or "Новый чат")[:200],
            format=_safe_format(payload.get("format")),
            stage=normalize_stage(str(stage or "")),
            focus=str(focus or ""),
            context={"objects": objects},
            created_by=uid,
        )
        if not cid:
            raise HTTPException(status_code=500, detail="не удалось создать чат")
        chat = await _stor().get_web_chat(cid)
        return {"chat": _chat_full(chat, [])}

    @app.get("/api/chats/{chat_id}")
    async def get_chat(chat_id: str, uid: int = Depends(auth)):
        chat = await _chat_or_404(chat_id)
        messages = await _stor().list_web_chat_messages(UUID(chat_id))
        return {"chat": _chat_full(chat, messages)}

    @app.patch("/api/chats/{chat_id}")
    async def patch_chat(
        chat_id: str, payload: Dict[str, Any] = Body(default={}), uid: int = Depends(auth)
    ):
        chat = await _chat_or_404(chat_id)
        fields: Dict[str, Any] = {}
        if "title" in payload:
            fields["title"] = str(payload.get("title") or "")[:200]
        if "format" in payload:
            fields["format"] = _safe_format(payload.get("format"))
        if "stage" in payload:
            fields["stage"] = normalize_stage(str(payload.get("stage") or ""))
        if "focus" in payload:
            fields["focus"] = str(payload.get("focus") or "")
        if "archived" in payload:
            fields["archived"] = bool(payload.get("archived"))
        if "objects" in payload:
            ctx = dict(chat.get("context") or {})
            ctx["objects"] = [r.id for r in parse_object_ids(payload.get("objects") or [])]
            fields["context"] = ctx
        if fields:
            await _stor().update_web_chat(UUID(chat_id), **fields)
        chat = await _stor().get_web_chat(UUID(chat_id))
        return {"chat": _chat_full(chat, [])}

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: str, uid: int = Depends(auth)):
        await _chat_or_404(chat_id)
        await _stor().delete_web_chat(UUID(chat_id))
        return {"ok": True}

    # ── Ход диалога ────────────────────────────────────────────────────────
    @app.post("/api/chats/{chat_id}/message")
    async def send_message(
        chat_id: str, payload: Dict[str, Any] = Body(default={}), uid: int = Depends(auth)
    ):
        chat = await _chat_or_404(chat_id)
        text = str((payload or {}).get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="пустое сообщение")
        from config import config

        user_id = uid  # расход токенов пишется на вошедшего админа

        history = await _stor().last_web_chat_messages(
            UUID(chat_id), limit=int(getattr(config, "WEB_HISTORY_MESSAGES", 20) or 20)
        )
        await _stor().insert_web_chat_message(chat_id=UUID(chat_id), role="user", text=text)
        if (chat.get("title") or "").strip() in ("", "Новый чат"):
            await _stor().update_web_chat(UUID(chat_id), title=text[:60])

        _cleanup_jobs()
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {
            "status": "running",
            "stage": "plan",
            "stage_title": _STAGE_TITLES["plan"],
            "started": time.time(),
            "chat_id": chat_id,
        }

        async def _work() -> None:
            def _on_stage(name: str, info: Dict[str, Any]) -> None:
                job = _JOBS.get(job_id)
                if job is not None:
                    job["stage"] = name
                    job["stage_title"] = _STAGE_TITLES.get(name, name)
                    job["stage_info"] = info

            try:
                tree = await build_tree(bot_app)
                result = await run_turn(
                    bot_app,
                    chat=chat,
                    user_text=text,
                    history=history,
                    user_id=user_id,
                    object_names=object_names(tree),
                    on_stage=_on_stage,
                )
                reply = result.text or "Пустой ответ модели. Попробуйте переформулировать."
                mid = await _stor().insert_web_chat_message(
                    chat_id=UUID(chat_id),
                    role="assistant",
                    text=reply,
                    model=result.model,
                    meta={"trace": result.trace},
                )
                job = _JOBS.get(job_id)
                if job is not None:
                    job.update(
                        {
                            "status": "done",
                            "message": {
                                "id": mid,
                                "role": "assistant",
                                "text": reply,
                                "model": result.model,
                                "meta": {"trace": result.trace},
                            },
                        }
                    )
            except Exception as e:
                logger.exception("web studio turn failed: %s", e)
                job = _JOBS.get(job_id)
                if job is not None:
                    job.update({"status": "error", "error": str(e)[:500]})

        asyncio.create_task(_work())
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str, uid: int = Depends(auth)):
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="задача не найдена")
        out = {
            "status": job.get("status"),
            "stage": job.get("stage"),
            "stage_title": job.get("stage_title"),
            "stage_info": job.get("stage_info") or {},
            "elapsed": int(time.time() - float(job.get("started") or time.time())),
        }
        if job.get("status") == "done":
            out["message"] = job.get("message")
            _JOBS.pop(job_id, None)
        elif job.get("status") == "error":
            out["error"] = job.get("error")
            _JOBS.pop(job_id, None)
        return out

    # ── Золотой фонд ───────────────────────────────────────────────────────
    @app.post("/api/chats/{chat_id}/messages/{message_id}/golden")
    async def to_golden(chat_id: str, message_id: int, uid: int = Depends(auth)):
        """Ответ, который понравился, становится образцом формата для следующих ходов."""
        chat = await _chat_or_404(chat_id)
        msg = await _stor().get_web_chat_message(int(message_id))
        if not msg or str(msg.get("role")) != "assistant":
            raise HTTPException(status_code=404, detail="ответ не найден")
        meta = dict(msg.get("meta") or {})
        if meta.get("golden"):
            return {"ok": True, "already": True}
        rag = getattr(bot_app, "rag_stack", None)
        if rag is None:
            raise HTTPException(status_code=503, detail="RAG не поднят, образец некуда сохранить")

        # Тема образца — последняя задача эксперта перед этим ответом.
        topic = ""
        for row in await _stor().list_web_chat_messages(UUID(chat_id)):
            if int(row.get("id") or 0) >= int(message_id):
                break
            if str(row.get("role")) == "user":
                topic = str(row.get("text") or "")
        topic = (topic or chat.get("title") or chat.get("focus") or "студия").strip()[:2000]

        lesson_keys = [
            ref.value
            for ref in parse_object_ids((chat.get("context") or {}).get("objects") or [])
            if ref.kind == "lesson"
        ]
        extra = {
            "schema_v": 2,
            "legacy": False,
            "seed": False,
            "product_id": str(chat.get("product_id") or active_product_id()),
            "format": str(chat.get("format") or ""),
            "stage": str(chat.get("stage") or ""),
            "lesson_key": lesson_keys[0] if len(lesson_keys) == 1 else "",
            "source_flow": "web_studio",
            "added_by": uid,
            "web_chat_id": str(chat.get("id")),
        }
        gid = await rag.golden.add_example_async(topic, str(msg.get("text") or ""), extra_metadata=extra)
        if not gid:
            raise HTTPException(status_code=500, detail="не удалось сохранить образец")
        meta["golden"] = True
        meta["golden_id"] = gid
        await _stor().update_web_chat_message_meta(int(message_id), meta)
        logger.info("web studio: ответ %s → золотой фонд (%s)", message_id, gid)
        return {"ok": True, "golden_id": gid}

    # ── Настройки по умолчанию ─────────────────────────────────────────────
    @app.post("/api/settings")
    async def settings(payload: Dict[str, Any] = Body(default={}), uid: int = Depends(auth)):
        pid = active_product_id()
        if "focus" in payload:
            await _stor().set_content_setting(pid, "focus", str(payload.get("focus") or ""))
        if "stage" in payload:
            await _stor().set_content_setting(
                pid, "stories_cycle_stage", normalize_stage(str(payload.get("stage") or ""))
            )
        return {"ok": True}

    # ── Статика ────────────────────────────────────────────────────────────
    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(HTTPException)
    async def _http_error(request, exc: HTTPException):  # type: ignore[override]
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    async def _chat_or_404(chat_id: str) -> Dict[str, Any]:
        try:
            cid = UUID(chat_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="плохой id чата")
        chat = await _stor().get_web_chat(cid)
        if not chat:
            raise HTTPException(status_code=404, detail="чат не найден")
        return chat

    return app


def _safe_format(value: Any) -> str:
    fid = str(value or "").strip()
    return fid if fid in FORMATS else "stories"


def _chat_brief(chat: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(chat.get("id")),
        "title": (chat.get("title") or "Без названия")[:200],
        "format": chat.get("format"),
        "stage": chat.get("stage"),
        "focus": chat.get("focus") or "",
        "objects": list((chat.get("context") or {}).get("objects") or []),
        "messages_count": int(chat.get("messages_count") or 0),
        "updated_at": str(chat.get("updated_at") or ""),
    }


def _chat_full(chat: Optional[Dict[str, Any]], messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not chat:
        return {}
    data = _chat_brief(chat)
    data["messages"] = [
        {
            "id": m.get("id"),
            "role": m.get("role"),
            "text": m.get("text") or "",
            "model": m.get("model") or "",
            "meta": m.get("meta") or {},
            "created_at": str(m.get("created_at") or ""),
        }
        for m in messages
    ]
    return data
