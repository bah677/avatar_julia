"""Извлечение тегов для RAG (группа и золотые примеры) — один код OpenAI."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TAG_SYSTEM = (
    "Выдели 3–5 ключевых тем из текста. Перечисли их через запятую. "
    "Теги краткие: «страх перемен», «работа с возражениями». Только список, без пояснений."
)


async def extract_content_tags(text: str, *, sample_max: int = 6000) -> str:
    """
    Теги через запятую для метаданных Chroma; пустая строка без ключа API или при ошибке.
    """
    from config import config

    key = (config.OPENAI_API_KEY or "").strip()
    if not key or not (text or "").strip():
        return ""

    sample = (text or "")[:sample_max]
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key)
        model = (config.RAG_TAG_MODEL or "gpt-4o-mini").strip()
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _TAG_SYSTEM},
                {"role": "user", "content": sample},
            ],
            max_tokens=150,
            temperature=0.25,
        )
        out = r.choices[0].message.content if r.choices else ""
        return (out or "").strip()[:500]
    except Exception as e:
        logger.warning("rag_tags extract_content_tags: %s", e)
        return ""
