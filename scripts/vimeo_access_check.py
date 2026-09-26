#!/usr/bin/env python3
"""Проверка доступа к Vimeo: метаданные, субтитры, файлы для Whisper.

  .venv/bin/python3 scripts/vimeo_access_check.py
  .venv/bin/python3 scripts/vimeo_access_check.py https://vimeo.com/836305758
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    url = (sys.argv[1] if len(sys.argv) > 1 else "https://vimeo.com/836305758").strip()
    from config import config
    from course.video_hosts.vimeo_api import (
        _token_from_config,
        parse_vimeo_ref,
        pick_audio_source,
        pick_texttrack,
        video_api_path,
        vimeo_get,
    )

    vid, hashed = parse_vimeo_ref(url)
    token = _token_from_config()
    print(f"url: {url}")
    print(f"id: {vid} hash: {hashed or '—'}")
    print(f"VIMEO_ACCESS_TOKEN: {'задан, len=' + str(len(token)) if token else 'нет'}")
    print(f"YTDLP_COOKIES_FILE: {config.YTDLP_COOKIES_FILE or 'нет'}")
    try:
        data = await vimeo_get(
            video_api_path(vid, hashed),
            params={
                "fields": (
                    "name,duration,privacy,link,files,download,play,status,is_playable"
                )
            },
        )
    except Exception as e:
        print(f"API: ошибка {e}")
        return 1
    privacy = data.get("privacy") or {}
    print(f"title: {data.get('name')}")
    print(f"duration_sec: {data.get('duration')}")
    print(f"privacy.view: {privacy.get('view')}")
    src = pick_audio_source(data)
    if src:
        print(
            f"файл для расшифровки: quality={src.get('quality')} "
            f"height={src.get('height')} size={src.get('size')} type={src.get('type')}"
        )
    else:
        print("файлов нет — без токена владельца (scope video_files) Whisper не запустится")
    try:
        tracks = await vimeo_get(video_api_path(vid, hashed) + "/texttracks")
        items = tracks.get("data") if isinstance(tracks.get("data"), list) else []
        picked = pick_texttrack([t for t in items if isinstance(t, dict)])
        print(f"субтитры: {len(items)}")
        if picked:
            print(
                f"  выбран: lang={picked.get('language')} name={picked.get('name')}"
            )
    except Exception as e:
        print(f"субтитры: ошибка {e}")
    if not src:
        print(
            "\nКак получить токен:\n"
            "1. Войти в Vimeo-аккаунт, куда загружены уроки.\n"
            "2. https://developer.vimeo.com/apps → Create App.\n"
            "3. Authenticated tokens → Generate Access Token.\n"
            "4. Scopes: public, private, video_files.\n"
            "5. Вписать в .env: VIMEO_ACCESS_TOKEN=...\n"
            "6. Накатить: ./scripts/deploy_prod.sh"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
