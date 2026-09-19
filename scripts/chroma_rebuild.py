#!/usr/bin/env python3
"""Пересборка expert_materials (v2) и content_cards из PostgreSQL и data/course/.

Старые чанки telegram_legacy не трогает. Карточки в Chroma перезаписываются целиком.

  python3 scripts/chroma_rebuild.py           # отчёт
  python3 scripts/chroma_rebuild.py --apply   # запись (нужен OPENAI_API_KEY)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("chroma_rebuild")


def _index_source(rs, src: Dict[str, Any], lesson: Optional[Dict[str, Any]]) -> int:
    from course.paths import source_dir
    from course.products import product_display_name
    from rag.material_index import format_chunk_heading, v2_base_metadata

    dest = source_dir(src["id"])
    lesson_key = (lesson or {}).get("lesson_key") or ""
    lesson_title = (lesson or {}).get("title") or ""
    product_name = product_display_name(src["product_id"])
    meta = v2_base_metadata(
        product_id=src["product_id"],
        source_id=str(src["id"]),
        source_kind=src.get("kind") or "other",
        origin=src.get("origin") or "disk",
        lesson_key=lesson_key,
        module_no=(lesson or {}).get("module_no"),
        recorded_on=str(src.get("recorded_on") or ""),
    )
    salt = f"course:{src['id']}:{src.get('disk_etag') or src.get('video_id') or ''}"
    rs.materials.delete_by_source(str(src["id"]))
    n = 0
    tr_path = dest / "transcript.json"
    pages_path = dest / "pages.json"
    posts_path = dest / "posts.json"
    if tr_path.is_file():
        data = json.loads(tr_path.read_text(encoding="utf-8"))
        segs = data.get("segments") or []

        def _h(start, end):
            return format_chunk_heading(
                product_name=product_name,
                lesson_key=lesson_key,
                lesson_title=lesson_title,
                kind=src.get("kind") or "",
                start_sec=start,
                end_sec=end,
            )

        n, _ = rs.materials.add_segments_text(
            segs,
            base_metadata=meta,
            source=str(src["id"])[:80],
            dedupe_salt=salt,
            heading_fn=_h,
        )
    elif pages_path.is_file() or posts_path.is_file():
        pages: List[dict]
        if posts_path.is_file() and not pages_path.is_file():
            posts = json.loads(posts_path.read_text(encoding="utf-8"))
            pages = [{"page": i + 1, "text": t} for i, t in enumerate(posts)]
        else:
            pages = json.loads(pages_path.read_text(encoding="utf-8"))
        page_tuples = [
            (int(p.get("page") or i + 1), p.get("text") or "") for i, p in enumerate(pages)
        ]
        heading = format_chunk_heading(
            product_name=product_name,
            lesson_key=lesson_key,
            lesson_title=lesson_title,
            kind=src.get("kind") or "",
        )
        n, _ = rs.materials.add_material_text(
            "",
            base_metadata=meta,
            source=str(src["id"])[:80],
            dedupe_salt=salt,
            heading=heading,
            pages=page_tuples,
        )
    return n


def _wipe_cards_collection(store) -> None:
    coll = store.cards_collection
    offset = 0
    page = 500
    while True:
        batch = coll.get(include=[], limit=page, offset=offset)
        ids = list(batch.get("ids") or [])
        if not ids:
            break
        coll.delete(ids=ids)
        if len(ids) < page:
            break


async def _run(*, apply: bool) -> int:
    from bot.integrations.rag_bridge import try_build_rag_stack
    from config import config, load_biblia_bot_config
    from course.products import EXPERT_PRODUCT_ID, get_registry
    from storage.user_storage import UserStorage

    rs = try_build_rag_stack(config)
    if rs is None:
        logger.error("RAG не поднят (RAG_ENABLED / OPENAI_API_KEY)")
        return 2

    bc = load_biblia_bot_config()
    stor = UserStorage(bc.database_url)
    await stor.initialize()
    try:
        pids = list(get_registry().ids()) + [EXPERT_PRODUCT_ID]
        sources = await stor.list_course_sources_by_product(pids)
        ready = [
            s
            for s in sources
            if (s.get("status") or "") in ("extracted", "indexed", "mining", "done")
        ]
        print(f"источников в PostgreSQL: {len(sources)}, к индексу: {len(ready)}")
        if not apply:
            cards_n = 0
            for pid in pids:
                cards_n += len(
                    await stor.list_content_cards(product_id=pid, status="active", limit=20_000)
                )
            print(f"карточек active: {cards_n}")
            print("запись не выполнялась (добавьте --apply)")
            return 0

        chunks_total = 0
        skipped = 0
        for src in ready:
            lesson = None
            if src.get("lesson_id"):
                lesson = await stor.get_course_lesson_by_id(int(src["lesson_id"]))
            n = _index_source(rs, src, lesson)
            if n:
                chunks_total += n
            else:
                skipped += 1
        print(f"чанков записано: {chunks_total}, без текста: {skipped}")

        _wipe_cards_collection(rs.vectors)
        cards_n = 0
        for pid in pids:
            for card in await stor.list_content_cards(
                product_id=pid, status="active", limit=20_000
            ):
                lesson_key = ""
                if card.get("lesson_id"):
                    les = await stor.get_course_lesson_by_id(int(card["lesson_id"]))
                    lesson_key = (les or {}).get("lesson_key") or ""
                ok = rs.materials.add_card_document(
                    card_id=str(card["id"]),
                    title=card.get("title") or "",
                    text=card.get("text") or "",
                    metadata={
                        "schema_v": 2,
                        "product_id": card.get("product_id") or pid,
                        "legacy": False,
                        "source_id": str(card.get("source_id") or ""),
                        "source_kind": "",
                        "lesson_key": lesson_key,
                        "card_id": str(card["id"]),
                        "type": card.get("type") or "idea",
                        "funnel_stage": card.get("funnel_stage") or "warmup",
                        "status": card.get("status") or "active",
                    },
                )
                if ok:
                    cards_n += 1
        print(f"карточек в Chroma: {cards_n}")
        return 0
    finally:
        await stor.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.apply:
        print("Перед --apply остановите бота.")
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
