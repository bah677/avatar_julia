"""Планирование RAG-поиска: запрос для Chroma и нужны ли отзывы клиентов."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

CONTENT_CATEGORY_TESTIMONIAL = "testimonial"
VOICE_SOURCE_CLIENT = "client"
VOICE_SOURCE_EXPERT = "expert"

_PLANNER_SYSTEM = """Ты планируешь семантический поиск по базе знаний эксперта (RAG).

На входе — последние реплики пользователя и контекст задачи (продукт, формат).
Верни ТОЛЬКО один JSON-объект (без markdown, без пояснений снаружи):
{
  "search_query": "1–3 предложения: суть запроса для поиска материалов ЭКСПЕРТА (стиль, структура, факты, подача)",
  "include_testimonials": true или false,
  "testimonial_search_query": "краткий запрос для поиска отзывов клиентов или пустая строка",
  "reason": "одно короткое предложение"
}

Правила для include_testimonials:
- true — если уместны отзывы/цитаты клиентов: соцдоказательства, кейсы, блок «что говорят клиенты», продающий текст с доказательствами, снятие возражений голосом клиентов, явная просьба цитировать отзывы.
- false — если нужен черновик от лица эксперта, теория, структура урока, сценарий без блока отзывов, внутренние правки стиля без соцдоказательств.

search_query и testimonial_search_query — на русском, по смыслу всего диалога, не только последней фразы."""


@dataclass(frozen=True)
class RetrievalPlan:
    search_query: str
    include_testimonials: bool
    testimonial_search_query: str = ""
    reason: str = ""


def build_dialogue_context_text(
    user_turns: List[str],
    *,
    task_summary: str = "",
    product: str = "",
    content_type: str = "",
    max_turns: int = 4,
) -> str:
    """Текст для планировщика и fallback-запроса Chroma."""
    parts: List[str] = []
    ts = (task_summary or "").strip()
    if ts:
        parts.append(f"Задача: {ts}")
    p = (product or "").strip()
    c = (content_type or "").strip()
    if p:
        parts.append(f"Продукт: {p}")
    if c:
        parts.append(f"Формат: {c}")
    turns = [str(t).strip() for t in (user_turns or []) if str(t).strip()]
    if max_turns > 0:
        turns = turns[-max_turns:]
    for i, t in enumerate(turns, 1):
        parts.append(f"Пользователь ({i}): {t}")
    return "\n".join(parts).strip()


def _default_plan(context_text: str) -> RetrievalPlan:
    q = (context_text or "").strip()
    return RetrievalPlan(
        search_query=q,
        include_testimonials=False,
        testimonial_search_query="",
        reason="fallback",
    )


def _parse_planner_json(raw: str) -> Optional[RetrievalPlan]:
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
    sq = str(data.get("search_query") or "").strip()
    if not sq:
        return None
    inc = data.get("include_testimonials")
    include = bool(inc) if isinstance(inc, bool) else str(inc).strip().lower() in (
        "1",
        "true",
        "yes",
    )
    tq = str(data.get("testimonial_search_query") or "").strip()
    if include and not tq:
        tq = sq
    reason = str(data.get("reason") or "").strip()
    return RetrievalPlan(
        search_query=sq[:2000],
        include_testimonials=include,
        testimonial_search_query=tq[:2000],
        reason=reason[:500],
    )


async def plan_retrieval_async(
    context_text: str,
    *,
    model: str = "gpt-4o-mini",
) -> RetrievalPlan:
    """
    LLM решает, нужны ли отзывы в выборке, и формирует search_query по контексту диалога.
    """
    ctx = (context_text or "").strip()
    if not ctx:
        return _default_plan("")

    from config import config

    key = (config.OPENAI_API_KEY or "").strip()
    if not key:
        logger.warning("plan_retrieval: нет OPENAI_API_KEY — fallback без отзывов")
        return _default_plan(ctx)

    tag_model = (getattr(config, "RAG_TAG_MODEL", None) or model or "gpt-4o-mini").strip()
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key)
        r = await client.chat.completions.create(
            model=tag_model,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user", "content": ctx},
            ],
            max_tokens=400,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        out = r.choices[0].message.content if r.choices else ""
        plan = _parse_planner_json(out or "")
        if plan:
            logger.info(
                "retrieval plan: testimonials=%s reason=%r",
                plan.include_testimonials,
                plan.reason[:120],
            )
            return plan
        logger.warning("plan_retrieval: не разобрали JSON: %r", (out or "")[:300])
    except Exception as e:
        logger.warning("plan_retrieval failed: %s", e)

    return _default_plan(ctx)
