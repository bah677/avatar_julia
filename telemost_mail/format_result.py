"""Текст ответа после индексации письма Телемоста в RAG."""

from __future__ import annotations

from html import escape as html_escape
from typing import Any, Dict, Iterable, Tuple

# (ключ в Chroma, подпись в Telegram)
_META_DISPLAY: Tuple[Tuple[str, str], ...] = (
    ("topic_title", "топик"),
    ("content_type", "тип"),
    ("content_category", "вид"),
    ("product", "продукт"),
    ("tags", "теги"),
    ("meeting_topic", "о встрече"),
    ("date", "дата"),
    ("source", "источник"),
    ("voice_source", "голос"),
    ("import_source", "импорт"),
)


def _filled(meta: Dict[str, Any], key: str) -> str:
    return str(meta.get(key) or "").strip()


def format_telemost_index_result_html(n: int, meta: Dict[str, Any]) -> str:
    lines = [f"Добавлено <b>{int(n)}</b> чанков в RAG"]
    for key, label in _META_DISPLAY:
        val = _filled(meta, key)
        if val:
            lines.append(
                f"· <b>{html_escape(label)}</b>: "
                f"<code>{html_escape(val[:200])}</code>"
            )
    if _filled(meta, "private_source_link"):
        lines.append("· <b>ссылка</b>: <code>private_source_link</code>")
    elif _filled(meta, "public_source_link"):
        lines.append("· <b>ссылка</b>: <code>public_source_link</code>")
    if _filled(meta, "telemost_imap_uid"):
        lines.append(
            f"· <b>uid почты</b>: <code>{html_escape(_filled(meta, 'telemost_imap_uid')[:40])}</code>"
        )
    filled_keys = _filled_metadata_keys(meta)
    if filled_keys:
        keys_s = ", ".join(html_escape(k) for k in filled_keys)
        lines.append(f"· <b>поля</b>: <code>{keys_s}</code>")
    return "\n".join(lines)


def _filled_metadata_keys(meta: Dict[str, Any]) -> Iterable[str]:
    skip_empty = {
        k
        for k in meta
        if meta.get(k) is not None and str(meta.get(k)).strip()
    }
    ordered: list[str] = []
    for key, _ in _META_DISPLAY:
        if key in skip_empty:
            ordered.append(key)
    for key in ("private_source_link", "public_source_link", "telemost_imap_uid"):
        if key in skip_empty and key not in ordered:
            ordered.append(key)
    for key in sorted(skip_empty):
        if key not in ordered:
            ordered.append(key)
    return ordered
