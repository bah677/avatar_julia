"""LLM: по подсказке пользователя, имени файла и началу транскрипта — метаданные RAG."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset(
    {"story", "educational", "webinar", "dialog", "manual_text", "testimonial"}
)

_SYSTEM = """Ты размечаешь аудио/текстовые материалы эксперта для базы знаний (RAG).

На входе:
1) Инструкция куратора — как понимать файлы в этой папке (имена, вступление в записи и т.д.)
2) Имя файла
3) Начало расшифровки (если есть)

Верни ТОЛЬКО JSON (без markdown):
{
  "title": "краткое название материала",
  "material_kind": "вид материала: молитва, эфир, урок, интервью, …",
  "content_type": "формат для поиска (как в топиках Telegram: «Молитвы», «Эфиры», …)",
  "content_category": "одно из: story, educational, webinar, dialog, manual_text, testimonial",
  "product": "продукт/линейка или пустая строка",
  "subtype": "подтип при необходимости или пустая строка",
  "tags": "3–6 тегов через запятую",
  "summary": "1–2 предложения: о чём запись",
  "reason": "одно короткое предложение — откуда взял классификацию"
}

Правила:
- Следуй инструкции куратора: если он пишет, что тип в названии файла — разбери имя.
- Если в начале записи диктор называет формат — доверяй вступлению.
- content_category: webinar для эфиров/разборов в прямом эфире, educational для уроков/молитв-обучений, manual_text по умолчанию.
- Не выдумывай факты, которых нет в имени файла и транскрипте."""


@dataclass(frozen=True)
class DiskMaterialMetadata:
    title: str
    material_kind: str
    content_type: str
    content_category: str
    product: str
    subtype: str
    tags: str
    summary: str
    reason: str = ""

    def as_chroma_metadata(self, *, source_label: str, remote_path: str) -> Dict[str, Any]:
        return {
            "source": source_label[:80],
            "content_type": (self.content_type or "аудио")[:500],
            "content_category": self.content_category or "manual_text",
            "product": (self.product or "general")[:500],
            "tags": (self.tags or "")[:500],
            "topic_title": (self.title or source_label)[:500],
            "material_kind": (self.material_kind or "")[:120],
            "subtype": (self.subtype or "")[:120],
            "voice_source": "expert",
            "import_source": "yandex_disk",
            "yandex_disk_path": remote_path[:500],
        }


def _parse_json(raw: str) -> Optional[DiskMaterialMetadata]:
    text = (raw or "").strip()
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    cat = str(data.get("content_category") or "manual_text").strip().lower()
    if cat not in _VALID_CATEGORIES:
        cat = "manual_text"

    return DiskMaterialMetadata(
        title=str(data.get("title") or "").strip()[:500],
        material_kind=str(data.get("material_kind") or "").strip()[:120],
        content_type=str(data.get("content_type") or "").strip()[:500],
        content_category=cat,
        product=str(data.get("product") or "").strip()[:500],
        subtype=str(data.get("subtype") or "").strip()[:120],
        tags=str(data.get("tags") or "").strip()[:500],
        summary=str(data.get("summary") or "").strip()[:2000],
        reason=str(data.get("reason") or "").strip()[:500],
    )


async def extract_disk_material_metadata(
    *,
    curator_hint: str,
    filename: str,
    transcript_head: str = "",
    default_product: str = "",
    default_content_type: str = "",
) -> DiskMaterialMetadata:
    """Вызов LLM; при ошибке — эвристика по имени файла."""
    from config import config

    hint = (curator_hint or "").strip()
    fname = (filename or "").strip()
    head = (transcript_head or "").strip()[:2500]

    user_parts = []
    if hint:
        user_parts.append(f"Инструкция куратора:\n{hint}")
    user_parts.append(f"Имя файла: {fname}")
    if head:
        user_parts.append(f"Начало расшифровки:\n{head}")
    if default_product or default_content_type:
        user_parts.append(
            f"Подсказки по умолчанию: product={default_product or '—'}, "
            f"content_type={default_content_type or '—'}"
        )
    user_block = "\n\n".join(user_parts)

    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    if key and user_block:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            r = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user_block},
                ],
                max_tokens=500,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            out = r.choices[0].message.content if r.choices else ""
            meta = _parse_json(out or "")
            if meta:
                if default_product and not meta.product:
                    meta = DiskMaterialMetadata(
                        title=meta.title,
                        material_kind=meta.material_kind,
                        content_type=meta.content_type or default_content_type,
                        content_category=meta.content_category,
                        product=default_product,
                        subtype=meta.subtype,
                        tags=meta.tags,
                        summary=meta.summary,
                        reason=meta.reason,
                    )
                if default_content_type and not meta.content_type:
                    meta = DiskMaterialMetadata(
                        title=meta.title,
                        material_kind=meta.material_kind,
                        content_type=default_content_type,
                        content_category=meta.content_category,
                        product=meta.product,
                        subtype=meta.subtype,
                        tags=meta.tags,
                        summary=meta.summary,
                        reason=meta.reason,
                    )
                logger.info(
                    "yandex_disk metadata %r: kind=%s product=%s",
                    fname,
                    meta.material_kind,
                    meta.product,
                )
                return meta
        except Exception as e:
            logger.warning("yandex_disk metadata LLM failed: %s", e)

    return _fallback_metadata(
        filename=fname,
        default_product=default_product,
        default_content_type=default_content_type,
    )


def _fallback_metadata(
    *,
    filename: str,
    default_product: str,
    default_content_type: str,
) -> DiskMaterialMetadata:
    low = (filename or "").lower()
    kind = "аудио"
    cat = "manual_text"
    if any(k in low for k in ("молитв", "prayer")):
        kind = "молитва"
        cat = "educational"
    elif any(k in low for k in ("эфир", "live", "stream")):
        kind = "эфир"
        cat = "webinar"
    return DiskMaterialMetadata(
        title=filename.rsplit(".", 1)[0] if filename else "аудио",
        material_kind=kind,
        content_type=default_content_type or kind,
        content_category=cat,
        product=default_product or "",
        subtype="",
        tags="",
        summary="",
        reason="fallback",
    )
