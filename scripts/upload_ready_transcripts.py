#!/usr/bin/env python3
"""Залить уже готовые локальные transcript.json на Яндекс.Диск.

  .venv/bin/python3 scripts/upload_ready_transcripts.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    from pathlib import Path as P
    import asyncpg

    def load_env():
        vals = {}
        for line in P(ROOT / ".env").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            vals[k.strip()] = v.strip().strip("'\"")
        return vals

    env = load_env()
    conn = await asyncpg.connect(
        f"postgresql://{env.get('DB_USER')}:{env.get('DB_PASSWORD')}"
        f"@{env.get('DB_HOST', 'localhost')}:{env.get('DB_PORT', '5432')}"
        f"/{env.get('BIBLIA_DB_NAME') or env.get('DB_NAME')}"
    )

    class _Stor:
        def __init__(self, c):
            self._c = c

        async def get_course_lesson_by_id(self, lesson_id):
            row = await self._c.fetchrow("SELECT * FROM course_lessons WHERE id=$1", int(lesson_id))
            return dict(row) if row else None

        async def list_course_sources_for_lesson(self, lesson_id):
            rows = await self._c.fetch(
                "SELECT * FROM course_sources WHERE lesson_id=$1 AND status<>'deleted'",
                int(lesson_id),
            )
            return [dict(r) for r in rows]

        async def upsert_course_lesson(self, **kw):
            return None

    from course.paths import source_dir
    from course.speech import segments_from_dicts
    from course.transcript_disk import upload_raw_transcript

    rows = await conn.fetch(
        """
        SELECT s.*, l.lesson_key
          FROM course_sources s
          JOIN course_lessons l ON l.id = s.lesson_id
         WHERE s.kind = 'lesson_video'
           AND s.chars_count > 0
           AND s.status <> 'deleted'
         ORDER BY l.module_no, l.lesson_no
        """
    )
    stor = _Stor(conn)
    n = 0
    for row in rows:
        src = dict(row)
        path = source_dir(src["id"]) / "transcript.json"
        if not path.is_file():
            print(f"skip {src.get('lesson_key')}: нет {path}")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        segs = segments_from_dicts(data.get("segments") or [])
        if not segs:
            print(f"skip {src.get('lesson_key')}: пустые сегменты")
            continue
        remote = await upload_raw_transcript(
            storage=stor,
            src=src,
            segments=segs,
            method=str(data.get("method") or src.get("text_method") or ""),
        )
        print(f"ok {src.get('lesson_key')} -> {remote}")
        n += 1
    await conn.close()
    print(f"uploaded {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
