"""Vimeo Data API: метаданные, субтитры, ссылки на файлы."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_API = "https://api.vimeo.com"
_ACCEPT = "application/vnd.vimeo.*+json;version=3.4.10"
_ID_RE = re.compile(
    r"(?:player\.)?vimeo\.com/(?:video/|channels/[\w]+/)?(\d+)(?:/([0-9a-f]{6,}))?",
    re.IGNORECASE,
)
_VIEWER_JWT: Tuple[str, float] = ("", 0.0)


def parse_vimeo_ref(url: str) -> Tuple[str, str]:
    """(video_id, unlisted_hash). Хеш также берётся из ?h=."""
    raw = (url or "").strip()
    if not raw:
        return "", ""
    m = _ID_RE.search(raw)
    vid = m.group(1) if m else ""
    hashed = (m.group(2) if m and m.group(2) else "") or ""
    if not hashed:
        qm = re.search(r"[?&]h=([0-9a-f]{6,})", raw, re.IGNORECASE)
        hashed = qm.group(1) if qm else ""
    return vid, hashed


def video_api_path(video_id: str, unlisted_hash: str = "") -> str:
    if unlisted_hash:
        return f"/videos/{video_id}:{unlisted_hash}"
    return f"/videos/{video_id}"


def _media_height(item: Dict[str, Any]) -> int:
    try:
        return int(item.get("height") or 0)
    except (TypeError, ValueError):
        return 0


def _media_size(item: Dict[str, Any]) -> int:
    try:
        return int(item.get("size") or item.get("size_short") or 0)
    except (TypeError, ValueError):
        return 0


def collect_media_files(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key in ("files", "download"):
        block = payload.get(key)
        if isinstance(block, list):
            out.extend(x for x in block if isinstance(x, dict) and x.get("link"))
    play = payload.get("play")
    if isinstance(play, dict):
        prog = play.get("progressive")
        if isinstance(prog, list):
            out.extend(x for x in prog if isinstance(x, dict) and x.get("link"))
        hls = play.get("hls")
        if isinstance(hls, dict) and hls.get("link"):
            out.append(
                {
                    "link": hls["link"],
                    "quality": "hls",
                    "type": "application/x-mpegURL",
                    "height": 10**6,
                }
            )
    return out


def pick_audio_source(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Самый лёгкий файл со звуком: аудиодорожка, иначе наименьшее mp4."""
    files = collect_media_files(payload)
    if not files:
        return None
    audio = [
        f
        for f in files
        if str(f.get("quality") or "").lower() == "audio"
        or str(f.get("type") or "").lower().startswith("audio/")
    ]
    if audio:
        return min(audio, key=_media_size)
    progressive = [
        f
        for f in files
        if str(f.get("quality") or "").lower() != "hls"
        and "mpegurl" not in str(f.get("type") or "").lower()
    ]
    pool = progressive or files
    return min(pool, key=lambda f: (_media_height(f) or 10**6, _media_size(f) or 10**12))


def pick_texttrack(tracks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not tracks:
        return None
    def score(t: Dict[str, Any]) -> tuple:
        lang = str(t.get("language") or "").lower()
        auto = "auto" in lang or "autogen" in str(t.get("name") or "").lower()
        ru = lang.startswith("ru")
        return (0 if ru else 1, 0 if not auto else 1, 0 if t.get("active") else 1)

    return min(tracks, key=score)


def _token_from_config() -> str:
    from config import config

    return (getattr(config, "VIMEO_ACCESS_TOKEN", "") or "").strip()


async def fetch_viewer_jwt() -> str:
    global _VIEWER_JWT
    token, exp = _VIEWER_JWT
    import time

    if token and exp - time.time() > 120:
        return token
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(
            "https://vimeo.com/_next/viewer",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        data = r.json()
    jwt = str(data.get("jwt") or "")
    if not jwt:
        raise RuntimeError("Vimeo: пустой viewer JWT")
    try:
        import base64

        payload = jwt.split(".")[1] + "=="
        raw = base64.urlsafe_b64decode(payload.encode())
        import json

        exp = float(json.loads(raw).get("exp") or 0)
    except Exception:
        exp = time.time() + 300
    _VIEWER_JWT = (jwt, exp)
    return jwt


async def auth_header() -> str:
    token = _token_from_config()
    if token:
        return f"Bearer {token}"
    jwt = await fetch_viewer_jwt()
    return f"jwt {jwt}"


async def vimeo_get(path: str, *, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    headers = {
        "Authorization": await auth_header(),
        "Accept": _ACCEPT,
        "User-Agent": "avatar-course/1.0",
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        r = await client.get(_API + path, headers=headers, params=params or {})
        if r.status_code == 401:
            kind = "токен" if _token_from_config() else "анонимный JWT"
            raise RuntimeError(f"Vimeo API 401 ({kind})")
        if r.status_code == 404:
            raise RuntimeError(f"Vimeo: видео не найдено ({path})")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}


def parse_recorded_on(raw: str):
    s = (raw or "")[:10]
    if len(s) >= 10:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None
