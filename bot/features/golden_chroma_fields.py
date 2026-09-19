"""
Метаданные золотых примеров в Chroma в том же духе, что и чанки expert_materials из группы.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bot.features.rag_group_metadata import (
    infer_dialog_role,
    resolve_content_type_product_category,
)
from bot.features.rag_tags import extract_content_tags

GOLDEN_FLOW_CREATIVE_TASK = "creative_task"
GOLDEN_FLOW_PRIVATE_DM = "private_dm"


@dataclass(frozen=True)
class GoldenSnapshot:
    """Снимок контекста на момент отправки ответа (для 👍)."""

    source_flow: str
    product: str = ""
    content_type: str = ""
    content_category: str = ""
    task_id: Optional[str] = None
    format: str = ""
    lesson_key: str = ""


def _build_source_label(
    snapshot: GoldenSnapshot,
    topic: str,
    added_by: int,
) -> str:
    if snapshot.source_flow == GOLDEN_FLOW_CREATIVE_TASK and snapshot.task_id:
        tid = (snapshot.task_id or "").strip()
        if tid:
            # как build_source_identifier: до ~80 символов
            return f"golden|task:{tid[:36]}"
    one_line = re.sub(r"\s+", " ", (topic or "").strip())
    tail = (one_line[:52] + "…") if len(one_line) > 52 else one_line
    base = f"golden|dm|u{added_by}|{tail}"
    return base[:80]


async def build_golden_extra_metadata_async(
    *,
    topic: str,
    answer: str,
    added_by: int,
    snapshot: GoldenSnapshot,
) -> Dict[str, Any]:
    """
    Поля вне topic/answer (их добавит GoldenExamplesStore.add_example).
    Согласованы с group_rag_indexer (source, content_type, content_category, product,
    tags, added_by, date, topic_title; опционально role; для creative — creative_task_id).
    """
    t = (topic or "").strip()
    a = (answer or "").strip()

    if snapshot.source_flow == GOLDEN_FLOW_PRIVATE_DM:
        ct, pr, cat = resolve_content_type_product_category(t)
    else:
        ct = (snapshot.content_type or "").strip()
        pr = (snapshot.product or "").strip()
        cat = (snapshot.content_category or "").strip()

    tags = await extract_content_tags(f"{t}\n\n{a}")
    date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    topic_title = t[:500]
    src = _build_source_label(snapshot, t, added_by)

    meta: Dict[str, Any] = {
        "source": src,
        "content_type": ct,
        "content_category": cat,
        "product": pr,
        "tags": tags,
        "added_by": added_by,
        "date": date_iso,
        "topic_title": topic_title,
        "golden_flow": snapshot.source_flow,
    }
    if snapshot.format:
        meta["format"] = snapshot.format
    if snapshot.lesson_key:
        meta["lesson_key"] = snapshot.lesson_key
    if snapshot.task_id:
        meta["creative_task_id"] = str(snapshot.task_id)[:128]

    role = infer_dialog_role(a, cat)
    if role:
        meta["role"] = role

    return meta
