"""Типы данных RAG-слоя без зависимостей от Telegram/БД."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChunkRecord:
    """Один чанк для записи в коллекцию expert_materials."""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


def normalize_chroma_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Chroma принимает в metadata только str | int | float | bool.
    Списки/прочее — JSON-строка или строка через запятую.
    """
    out: Dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list):
            out[k] = ", ".join(str(x) for x in v)
        else:
            out[k] = str(v)
    return out


_META_SKIP_KEYS = frozenset({
    "chunk_index", "added_by", "schema_v", "legacy", "source_id",
    "module_no", "origin",
})


def format_retrieval_line(
    meta: Dict[str, Any],
    chunk_text: str,
) -> str:
    """Заголовок чанка без служебных полей + текст."""
    text = (chunk_text or "").strip()
    if text.startswith("["):
        return text
    parts: List[str] = []
    product = str(meta.get("product_id") or meta.get("product") or "").strip()
    lesson = str(meta.get("lesson_key") or "").strip()
    kind = str(meta.get("source_kind") or meta.get("content_type") or "").strip()
    if product:
        parts.append(product)
    if lesson:
        parts.append(f"урок {lesson}")
    if kind:
        parts.append(kind)
    page = meta.get("page")
    start = meta.get("start_sec")
    if page is not None and str(page).strip():
        parts.append(f"стр. {page}")
    elif start is not None and str(start).strip():
        try:
            s = int(float(start))
            parts.append(f"{s // 60}:{s % 60:02d}")
        except (TypeError, ValueError):
            pass
    header = " · ".join(parts) if parts else "материал"
    return f"[{header}]\n{text}"


_META_KEY_LABELS = {
    "source": "источник",
    "content_type": "тип",
    "content_category": "вид",
    "product": "продукт",
    "tags": "теги",
    "date": "дата",
    "topic_title": "топик",
    "public_source_link": "публичная ссылка",
    "private_source_link": "приватная ссылка",
    "role": "роль",
    "voice_source": "голос",
}


def format_retrieval_sections(
    expert_block: str,
    testimonial_block: str,
) -> str:
    """Склейка блоков для промпта с явным разделением эксперт / клиент."""
    parts: List[str] = []
    ex = (expert_block or "").strip()
    te = (testimonial_block or "").strip()
    if ex:
        parts.append("=== Материалы эксперта (стиль, мысли, структура — опирайся на них) ===\n")
        parts.append(ex)
    if te:
        parts.append(
            "\n\n=== Отзывы клиентов (только цитаты и доказательства; НЕ копируй их тон как голос эксперта) ===\n"
        )
        parts.append(te)
    if not parts:
        return "(фрагменты из базы не найдены — опирайся на диалог)"
    return "\n".join(parts)
