"""Индексация источника курса в expert_materials (транскрипт + описание)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from course.paths import source_dir
from course.products import product_display_name
from rag.material_index import format_chunk_heading, v2_base_metadata


def user_description(src: Dict[str, Any]) -> str:
    meta = src.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("description") or "").strip()[:4000]


def add_source_to_materials(
    rs: Any,
    src: Dict[str, Any],
    *,
    lesson: Optional[Dict[str, Any]] = None,
) -> int:
    dest = source_dir(src["id"])
    lesson_key = (lesson or {}).get("lesson_key") or ""
    lesson_title = (lesson or {}).get("title") or ""
    module_no = src.get("module_no")
    if module_no is None:
        module_no = (lesson or {}).get("module_no")
    product_name = product_display_name(src["product_id"])
    meta = v2_base_metadata(
        product_id=src["product_id"],
        source_id=str(src["id"]),
        source_kind=src.get("kind") or "other",
        origin=src.get("origin") or "disk",
        lesson_key=lesson_key,
        module_no=module_no,
        recorded_on=str(src.get("recorded_on") or ""),
    )
    salt = f"course:{src['id']}:{src.get('disk_etag') or src.get('video_id') or ''}"
    rs.materials.delete_by_source(str(src["id"]))
    n = 0
    tr_path = dest / "transcript.json"
    pages_path = dest / "pages.json"
    posts_path = dest / "posts.json"
    heading = format_chunk_heading(
        product_name=product_name,
        lesson_key=lesson_key,
        lesson_title=lesson_title,
        module_no=module_no,
        kind=src.get("kind") or "",
    )
    if tr_path.is_file():
        data = json.loads(tr_path.read_text(encoding="utf-8"))
        segs = data.get("segments") or []

        def _h(start, end):
            return format_chunk_heading(
                product_name=product_name,
                lesson_key=lesson_key,
                lesson_title=lesson_title,
                module_no=module_no,
                kind=src.get("kind") or "",
                start_sec=start,
                end_sec=end,
            )

        added, _ = rs.materials.add_segments_text(
            segs,
            base_metadata=meta,
            source=str(src["id"])[:80],
            dedupe_salt=salt,
            heading_fn=_h,
        )
        n += added
    elif pages_path.is_file() or posts_path.is_file():
        if posts_path.is_file() and not pages_path.is_file():
            posts = json.loads(posts_path.read_text(encoding="utf-8"))
            pages = [{"page": i + 1, "text": t} for i, t in enumerate(posts)]
        else:
            pages = json.loads(pages_path.read_text(encoding="utf-8"))
        page_tuples = [
            (int(p.get("page") or i + 1), p.get("text") or "") for i, p in enumerate(pages)
        ]
        added, _ = rs.materials.add_material_text(
            "",
            base_metadata=meta,
            source=str(src["id"])[:80],
            dedupe_salt=salt,
            heading=heading,
            pages=page_tuples,
        )
        n += added
    desc = user_description(src)
    if desc:
        added, _ = rs.materials.add_material_text(
            desc,
            base_metadata=meta,
            source=str(src["id"])[:80],
            dedupe_salt=f"{salt}:desc",
            heading=format_chunk_heading(
                product_name=product_name,
                lesson_key=lesson_key,
                lesson_title=lesson_title,
                module_no=module_no,
                kind=src.get("kind") or "",
                extra="описание",
            ),
        )
        n += added
    return n
