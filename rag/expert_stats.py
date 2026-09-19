"""
Сводка по коллекции expert_materials: иерархия продукт → content_category → content_type, число чанков.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import DefaultDict, List, Tuple

from rag.vector_store import VectorStoreService

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SCAN = 100_000
_PAGE = 2_000


@dataclass
class CategoryBranch:
    """Один вид контента и набор типов с количествами чанков."""

    category_label: str
    by_content_type: List[Tuple[str, int]]  # (content_type, chunks)


@dataclass
class ProductBranch:
    """Один продукт и вложенные категории."""

    product_label: str
    categories: List[CategoryBranch]


@dataclass
class ExpertMaterialsStats:
    total_chunks: int
    truncated: bool
    hierarchy: List[ProductBranch]


def _norm_meta(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _display_label(s: str) -> str:
    return s if s else "(не задано)"


def compute_expert_materials_statistics(
    store: VectorStoreService,
    *,
    max_scan: int = _DEFAULT_MAX_SCAN,
) -> ExpertMaterialsStats:
    """
    Считает чанки по тройке (product, content_category, content_type), собирает дерево.
    """
    coll = store.expert_collection
    # product -> category -> content_type -> count
    nested: DefaultDict[str, DefaultDict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    total = 0
    offset = 0
    truncated = False

    while offset < max_scan:
        try:
            raw = coll.get(include=["metadatas"], limit=_PAGE, offset=offset)
        except Exception as e:
            logger.error("expert_stats get: %s", e, exc_info=True)
            break
        metas = raw.get("metadatas") or []
        if not metas:
            break
        for m in metas:
            if not m:
                continue
            total += 1
            p = _norm_meta(m.get("product"))
            c = _norm_meta(m.get("content_category"))
            t = _norm_meta(m.get("content_type"))
            nested[p][c][t] += 1
        offset += len(metas)
        if len(metas) < _PAGE:
            break
        if offset >= max_scan:
            truncated = True
            break

    hierarchy: List[ProductBranch] = []

    for p_key, cat_map in nested.items():
        p_label = _display_label(p_key)
        cat_branches: List[CategoryBranch] = []

        for c_key, type_counter in cat_map.items():
            c_label = _display_label(c_key)
            # типы: по убыванию числа чанков, затем по названию
            type_rows = sorted(
                type_counter.items(),
                key=lambda it: (-it[1], _display_label(it[0])),
            )
            typed: List[Tuple[str, int]] = [
                (_display_label(t_key), n) for t_key, n in type_rows
            ]
            cat_branches.append(CategoryBranch(category_label=c_label, by_content_type=typed))

        # категории внутри продукта: по убыванию суммы чанков
        cat_branches.sort(
            key=lambda br: (
                -sum(n for _, n in br.by_content_type),
                br.category_label.replace("(не задано)", "\uffff"),
            )
        )

        hierarchy.append(ProductBranch(product_label=p_label, categories=cat_branches))

    # продукты: по убыванию суммы чанков
    def product_total(pb: ProductBranch) -> int:
        return sum(
            sum(n for _, n in br.by_content_type) for br in pb.categories
        )

    hierarchy.sort(
        key=lambda pb: (
            -product_total(pb),
            pb.product_label.replace("(не задано)", "\uffff"),
        )
    )

    return ExpertMaterialsStats(
        total_chunks=total,
        truncated=truncated,
        hierarchy=hierarchy,
    )


def format_expert_stats_html(stats: ExpertMaterialsStats, *, golden_count: int = -1) -> str:
    """HTML для Telegram."""

    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    now_msk = datetime.now(timezone(timedelta(hours=3)))
    ts = now_msk.strftime("%d.%m.%Y %H:%M MSK")

    parts: List[str] = [
        f"📊 <b>Сводка по базе RAG</b> (коллекция <code>expert_materials</code>)",
        f"🕐 <code>{ts}</code>",
        f"Всего <b>чанков</b>: <code>{stats.total_chunks}</code>",
    ]
    if golden_count >= 0:
        parts.append(
            f"Коллекция <code>golden_examples</code>: <code>{golden_count}</code> записей"
        )
    if stats.truncated:
        parts.append(
            "<i>Достигнут лимит сканирования — счётчики могут быть неполными.</i>"
        )
    parts.append("")
    parts.append("<b>Продукт → вид → тип</b>")
    parts.append("")

    if not stats.hierarchy:
        parts.append("<i>Нет проиндексированных чанков.</i>")
        return "\n".join(parts)

    max_lines = 120
    lines_out = 0
    truncated_fmt = False

    for pb in stats.hierarchy:
        if lines_out >= max_lines:
            truncated_fmt = True
            break
        parts.append(f"- <b>{esc(pb.product_label)}</b>")
        lines_out += 1
        for cb in pb.categories:
            if lines_out >= max_lines:
                truncated_fmt = True
                break
            parts.append(f"-- <b>{esc(cb.category_label)}</b>")
            lines_out += 1
            for type_label, n in cb.by_content_type:
                if lines_out >= max_lines:
                    truncated_fmt = True
                    break
                parts.append(
                    f"--- {esc(type_label)} — <code>{n}</code> чанков"
                )
                lines_out += 1
            if truncated_fmt:
                break
        if truncated_fmt:
            break

    if truncated_fmt:
        parts.append("<i>… вывод обрезан (слишком много строк). Уточните фильтрами в данных.</i>")

    return "\n".join(parts)
