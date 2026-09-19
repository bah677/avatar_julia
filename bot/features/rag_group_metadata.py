"""
Метаданные чанков RAG при индексации из форум-группы.

Форум-топик: ``<произвольная левая метка> | <продукт>``.

Пример ``Продающие сториз | Клуб Материн``:

- ``content_type`` — дословно левая часть (человекочитаемая метка топика).
- ``product`` — дословно правая часть.
- ``content_category`` — одно из story / educational / webinar / dialog / manual_text
  по эвристикам по левой части (как раньше для типа).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import timezone
from typing import Any, Dict, Optional, Tuple, FrozenSet

from aiogram.types import Message

logger = logging.getLogger(__name__)

# Допустимые значения ``content_category`` (эвристика + retrieval по категории).
CONTENT_TYPES = frozenset(
    {"story", "educational", "webinar", "dialog", "manual_text", "testimonial"}
)

CONTENT_CATEGORY_TESTIMONIAL = "testimonial"
VOICE_SOURCE_CLIENT = "client"
VOICE_SOURCE_EXPERT = "expert"

_META_STR_MAX = 500


def _rag_indexer_debug_env() -> bool:
    v = (os.getenv("RAG_INDEXER_DEBUG") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    return (os.getenv("LOG_LEVEL") or "").strip().upper() == "DEBUG"


def message_in_rag_groups_scope(
    message: Message,
    groups_map: Dict[int, Optional[FrozenSet[int]]],
) -> bool:
    """Сообщение из чата/топика, перечисленных в ``RAG_GROUPS`` или ``RAG_TESTIMONIAL_GROUPS``."""
    if not groups_map:
        return False
    chat_id = message.chat.id
    allow = groups_map.get(chat_id)
    if allow is None and chat_id not in groups_map:
        return False
    if allow is None:
        return True
    tid = message.message_thread_id
    if tid is None:
        return False
    return int(tid) in allow


def testimonial_metadata_overrides() -> Dict[str, str]:
    """Принудительные поля для чанков из групп отзывов."""
    return {
        "content_category": CONTENT_CATEGORY_TESTIMONIAL,
        "voice_source": VOICE_SOURCE_CLIENT,
        "role": "client",
    }


def slug_product_label(s: str, max_len: int = 64) -> str:
    raw = (s or "").strip().lower()
    raw = re.sub(r"[^\w\s\-]+", "", raw, flags=re.UNICODE)
    raw = re.sub(r"\s+", "_", raw).strip("_")
    if not raw:
        return "general"
    return raw[:max_len]


def parse_forum_topic_line(topic_title: str) -> Tuple[str, str, str]:
    """
    Разбор строки топика.

    Returns:
        (left_type_label, right_product_label, full_raw)
    """
    full = (topic_title or "").strip()
    if "|" not in full:
        return "", "", full
    left, _, right = full.partition("|")
    return left.strip(), right.strip(), full


def infer_content_category(left_label: str, full_topic_fallback: str) -> str:
    """
    Эвристическая категория по подстрокам в левой части топика (или во всём названии).

    Результат — одно из ``CONTENT_TYPES``, пишется в ``content_category``.
    """
    blob = (left_label or full_topic_fallback or "").lower()
    if any(
        k in blob
        for k in ("отзыв", "review", "testimonial", "соцдоказ", "кейс клиент")
    ):
        return CONTENT_CATEGORY_TESTIMONIAL
    if any(k in blob for k in ("вебинар", "webinar", "расшифров")):
        return "webinar"
    if any(k in blob for k in ("сторис", "story", "сториз", "продающ")):
        return "story"
    if any(k in blob for k in ("диалог", "переписк", "dialog")):
        return "dialog"
    if any(
        k in blob
        for k in ("обуч", "теор", "курс", "модул", "educational", "лекци", "урок")
    ):
        return "educational"
    return "manual_text"


def resolve_content_type_product_category(topic_title: str) -> Tuple[str, str, str]:
    """
    Returns:
        (content_type, product, content_category).

    Если в названии топика есть ``|``:

    - ``content_type`` — левая часть (как в Telegram), до ``_META_STR_MAX`` символов;
    - ``product`` — правая часть, так же обрезка;
    - ``content_category`` — эвристика по левой части (и контексту всей строки).

    Если ``|`` нет: ``content_type`` = всё название топика; ``product`` = slug от названия
    (компактный ключ); ``content_category`` — эвристика по всей строке.
    """
    raw = (topic_title or "").strip()
    left, right, full = parse_forum_topic_line(topic_title)
    has_pipe = "|" in raw

    if has_pipe:
        ctype = (left or "").strip()[:_META_STR_MAX] or "unknown"
        prod = (right or "").strip()[:_META_STR_MAX] or "general"
        category = infer_content_category(left, full)
    else:
        ctype = (full or "").strip()[:_META_STR_MAX] or "unknown"
        prod = slug_product_label(full)
        category = infer_content_category("", full)

    if category not in CONTENT_TYPES:
        category = "manual_text"

    if _rag_indexer_debug_env():
        logger.debug(
            "rag_group_metadata: topic_title=%r -> left=%r right=%r full=%r "
            "pipe=%s -> content_type=%r product=%r content_category=%s",
            topic_title,
            left,
            right,
            full,
            has_pipe,
            ctype,
            prod,
            category,
        )
    return ctype, prod, category


def resolve_content_type_and_product(topic_title: str) -> Tuple[str, str]:
    """Обратная совместимость: (content_type, product) без ``content_category``."""
    ct, pr, _ = resolve_content_type_product_category(topic_title)
    return ct, pr


def message_date_iso_utc(message: Message) -> str:
    """Дата сообщения в ISO 8601 (UTC, с суффиксом Z при naive UTC)."""
    dt = message.date
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def message_date_compact(message: Message) -> str:
    """YYYY-MM-DD для префиксов в source."""
    return message_date_iso_utc(message)[:10]


def telegram_internal_message_link(message: Message) -> str:
    """
    Ссылка на сообщение в супергруппе/канале.

    Публичный username: ``https://t.me/<username>/<id>``.
    Приватная супергруппа: ``https://t.me/c/<internal>/<id>``.
    """
    chat = message.chat
    mid = message.message_id
    un = (chat.username or "").strip()
    if un:
        return f"https://t.me/{un}/{mid}"
    cid = chat.id
    s = str(cid)
    if s.startswith("-100") and len(s) > 4:
        return f"https://t.me/c/{s[4:]}/{mid}"
    return ""


def build_source_identifier(message: Message, raw_text: str, has_file_media: bool) -> str:
    """
    Идентификатор первоисточника: имя файла (медиа) или до 80 символов текста;
    для медиа без имени — дата + превью текста (до 80 символов суммарно по смыслу).
    """
    rt = (raw_text or "").strip()
    one_line = re.sub(r"\s+", " ", rt)
    preview = one_line[:80] + ("…" if len(one_line) > 80 else "")

    fn = ""
    if message.document and message.document.file_name:
        fn = (message.document.file_name or "").strip()
    elif message.video and message.video.file_name:
        fn = (message.video.file_name or "").strip()
    elif message.audio and message.audio.file_name:
        fn = (message.audio.file_name or "").strip()
    elif message.animation and message.animation.file_name:
        fn = (message.animation.file_name or "").strip()
    if fn:
        return fn[:80]

    dcompact = message_date_compact(message)

    if has_file_media:
        if preview:
            prefix = f"{dcompact}_" if dcompact else ""
            room = max(1, 80 - len(prefix))
            body = preview[:room]
            return (prefix + body)[:80]
        sub = (
            "photo"
            if message.photo
            else "video"
            if message.video
            else "voice"
            if message.voice
            else "audio"
            if message.audio
            else "document"
            if message.document
            else "media"
        )
        if dcompact:
            return f"{sub}_{dcompact}"[:80]
        return sub[:80]

    if preview:
        return preview[:80]
    return "text"


def infer_dialog_role(raw_text: str, content_category: str) -> Optional[str]:
    """
    Опционально: при ``content_category == "dialog"`` — client / expert по маркерам строк.
    """
    if (content_category or "").strip().lower() != "dialog":
        return None
    t = raw_text or ""
    client_m = len(re.findall(r"(?im)^\s*(клиент|client)\s*[:：]", t))
    expert_m = len(
        re.findall(r"(?im)^\s*(эксперт|expert|коуч|ведущ(ий|ая)?)\s*[:：]", t)
    )
    if client_m and not expert_m:
        return "client"
    if expert_m and not client_m:
        return "expert"
    return None
