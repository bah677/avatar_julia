"""Паспорт голоса, правила из 👎, первое наполнение золотого фонда."""

from __future__ import annotations

import hashlib
import logging
from typing import List, Optional

from course.llm import CourseLLM
from course.products import EXPERT_PRODUCT_ID, active_product_id
from openai_client.content_prompts import STYLE_SYSTEM, feedback_fold_system

logger = logging.getLogger(__name__)


def source_hash(parts: List[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\n---\n")
    return h.hexdigest()[:24]


def forbidden_words(style_text: str) -> List[str]:
    for line in (style_text or "").splitlines():
        if line.strip().casefold().startswith("запретные:"):
            rest = line.split(":", 1)[1]
            return [w.strip() for w in rest.split(",") if w.strip()]
    return []


async def build_style_passport(
    *,
    posts_text: str,
    expert_info: str,
    product_info: str,
    speech_samples: str,
    feedback_rules: str,
    llm: CourseLLM,
    user_id: int,
) -> str:
    from config import config

    model = getattr(config, "STYLE_MODEL", "gpt-4o")
    user = (
        f"Об эксперте:\n{expert_info or '—'}\n\n"
        f"О продукте:\n{product_info or '—'}\n\n"
        f"Посты:\n{(posts_text or '')[:20000]}\n\n"
        f"Речь с уроков:\n{(speech_samples or '')[:6000]}\n\n"
        f"Правила из 👎:\n{feedback_rules or '—'}"
    )
    text = await llm.complete(
        model=model,
        messages=[
            {"role": "system", "content": STYLE_SYSTEM},
            {"role": "user", "content": user},
        ],
        user_id=user_id,
        temperature=0.3,
        max_tokens=4000,
        request_kind="course_style",
    )
    return text or ""


async def fold_feedback_rules(
    reasons: List[str],
    llm: CourseLLM,
    user_id: int,
) -> List[str]:
    from config import config

    model = getattr(config, "STYLE_MODEL", "gpt-4o")
    data = await llm.complete_json(
        model=model,
        messages=[
            {"role": "system", "content": feedback_fold_system()},
            {"role": "user", "content": "\n".join(f"- {r}" for r in reasons)},
        ],
        user_id=user_id,
        temperature=0.2,
        max_tokens=800,
        request_kind="course_feedback_fold",
    )
    rules = data.get("rules") or []
    return [str(x).strip() for x in rules if str(x).strip()][:7]


def platform_to_format(platform: str) -> str:
    p = (platform or "").strip().lower()
    return {
        "telegram": "tg_post",
        "tg": "tg_post",
        "instagram": "ig_post",
        "ig": "ig_post",
    }.get(p, "tg_post")
