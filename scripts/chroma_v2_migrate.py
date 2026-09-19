#!/usr/bin/env python3
"""Шаг 1 переноса старых чанков expert_materials: метаданные v2 без новых эмбеддингов.

Остановить бота и скопировать chroma_data/ перед запуском с --apply.

  python3 scripts/chroma_v2_migrate.py            # отчёт, без записи
  python3 scripts/chroma_v2_migrate.py --apply    # collection.update метаданных
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course.disk_layout import parse_lesson_key_from_text  # noqa: E402
from course.products import match_product_alias  # noqa: E402

logger = logging.getLogger("chroma_v2_migrate")

PAGE = 200


def _persist_dir() -> Path:
    from config import config

    return Path(config.resolved_chroma_persist_dir)


def _collection_name() -> str:
    from config import config

    return getattr(config, "RAG_EXPERT_COLLECTION", None) or "expert_materials"


def _infer_product_id(meta: Dict[str, Any]) -> str:
    for key in ("product_id", "product"):
        raw = str(meta.get(key) or "").strip()
        if not raw:
            continue
        if key == "product_id" and raw in ("mbt", "magiya", "materinstvo", "_expert"):
            return raw
        mapped = match_product_alias(raw)
        if mapped:
            return mapped
    return ""


def _infer_origin_and_kind(meta: Dict[str, Any]) -> Tuple[str, str]:
    blob = " ".join(
        str(meta.get(k) or "") for k in ("source", "topic_title", "group_message_link")
    ).lower()
    if "youtu" in blob:
        return "telegram_legacy", "lesson_video"
    return "telegram_legacy", "other"


def _infer_lesson_key(meta: Dict[str, Any], document: str) -> str:
    head = (document or "")[:1500]
    key = parse_lesson_key_from_text(head)
    if key:
        return key
    blob = " ".join(
        str(meta.get(k) or "") for k in ("source", "topic_title")
    )
    return parse_lesson_key_from_text(blob)


def _module_no(lesson_key: str) -> Optional[int]:
    if not lesson_key or "." not in lesson_key:
        return None
    try:
        return int(lesson_key.split(".", 1)[0])
    except ValueError:
        return None


def build_v1_metadata(old: Dict[str, Any], document: str) -> Dict[str, Any]:
    meta = dict(old or {})
    product_id = _infer_product_id(meta)
    origin, kind = _infer_origin_and_kind(meta)
    lesson_key = _infer_lesson_key(meta, document)
    meta["schema_v"] = 1
    meta["legacy"] = True
    meta["origin"] = origin
    meta["product_id"] = product_id
    meta["source_kind"] = kind
    meta["lesson_key"] = lesson_key or ""
    mod = _module_no(lesson_key)
    if mod is not None:
        meta["module_no"] = mod
    elif "module_no" in meta and meta["module_no"] in (None, ""):
        meta.pop("module_no", None)
    return meta


def _chroma_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    from rag.types import normalize_chroma_metadata

    return normalize_chroma_metadata(meta)


def migrate(*, apply: bool, persist: Optional[Path] = None) -> int:
    import chromadb

    path = persist or _persist_dir()
    name = _collection_name()
    if not path.exists():
        logger.error("Chroma не найдена: %s", path)
        return 2

    client = chromadb.PersistentClient(path=str(path))
    try:
        coll = client.get_collection(name=name)
    except Exception as e:
        logger.error("Коллекция %s: %s", name, e)
        return 2

    total = 0
    updated = 0
    skipped_v2 = 0
    by_product: Counter[str] = Counter()
    with_lesson = 0
    offset = 0
    while True:
        batch = coll.get(
            include=["documents", "metadatas"],
            limit=PAGE,
            offset=offset,
        )
        ids: List[str] = list(batch.get("ids") or [])
        if not ids:
            break
        docs = list(batch.get("documents") or [])
        metas = list(batch.get("metadatas") or [])
        new_ids: List[str] = []
        new_metas: List[Dict[str, Any]] = []
        for i, cid in enumerate(ids):
            doc = docs[i] if i < len(docs) else ""
            old = metas[i] if i < len(metas) else {}
            if not isinstance(old, dict):
                old = {}
            if int(old.get("schema_v") or 0) >= 2:
                skipped_v2 += 1
                pid = str(old.get("product_id") or "") or "(v2 без product_id)"
                by_product[pid] += 1
                if old.get("lesson_key"):
                    with_lesson += 1
                continue
            fresh = _chroma_meta(build_v1_metadata(old, str(doc or "")))
            pid = str(fresh.get("product_id") or "") or "(нет)"
            by_product[pid] += 1
            if fresh.get("lesson_key"):
                with_lesson += 1
            new_ids.append(cid)
            new_metas.append(fresh)
        total += len(ids)
        updated += len(new_ids)
        if apply and new_ids:
            coll.update(ids=new_ids, metadatas=new_metas)
        offset += len(ids)
        if offset > 100_000:
            logger.warning("останов по лимиту offset")
            break
        logger.info("обработано %s…", total)

    print(f"чанков: {total}")
    print("по product_id:")
    for pid, n in sorted(by_product.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {pid}: {n}")
    print(f"с lesson_key: {with_lesson}")
    print(f"к обновлению (schema_v=1): {updated}")
    if skipped_v2:
        print(f"пропущено schema_v>=2: {skipped_v2}")
    if not apply:
        print("запись не выполнялась (добавьте --apply)")
    else:
        print("метаданные записаны")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="записать метаданные")
    p.add_argument("--persist", type=Path, default=None, help="путь к chroma_data")
    args = p.parse_args()
    if args.apply:
        print("Перед --apply остановите бота и скопируйте chroma_data/")
    return migrate(apply=args.apply, persist=args.persist)


if __name__ == "__main__":
    sys.exit(main())
