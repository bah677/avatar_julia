"""Сборка промпта, генерация черновика, правки, проверки после генерации."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Sequence

from bot.utils.telegram_html import looks_like_telegram_html, sanitize_telegram_html
from course.formats import FormatSpec, format_prompt_block, get_format
from course.llm import CourseLLM, is_deepseek_model
from course.products import EXPERT_PRODUCT_ID, active_product_id, product_display_name
from course.style import forbidden_words
from openai_client.content_prompts import writer_static_prefix, writer_task_block

logger = logging.getLogger(__name__)


def _len_ok(text: str, spec: FormatSpec) -> bool:
    n = len(text or "")
    if spec.min_chars and n < spec.min_chars:
        return False
    if spec.max_chars and n > spec.max_chars + 80:
        return False
    return True


def _has_forbidden(text: str, words: Sequence[str]) -> List[str]:
    found = []
    low = (text or "").casefold()
    for w in words:
        ww = (w or "").strip()
        if not ww:
            continue
        if re.search(rf"(?i)\b{re.escape(ww)}\b", text or ""):
            found.append(ww)
    return found


def check_draft(text: str, spec: FormatSpec, style_text: str) -> List[str]:
    issues = []
    if not _len_ok(text, spec):
        issues.append(
            f"длина {len(text)} не в диапазоне {spec.min_chars}–{spec.max_chars}"
        )
    bad = _has_forbidden(text, forbidden_words(style_text))
    if bad:
        issues.append("запретные слова: " + ", ".join(bad))
    if spec.markup == "telegram_html":
        sanitized = sanitize_telegram_html(text)
        if "<" in (text or "") and not looks_like_telegram_html(text):
            # если модель дала кривой HTML
            if sanitized != text and "<" in text:
                issues.append("некорректный Telegram-HTML")
    return issues


async def write_draft(
    *,
    format_id: str,
    expert_info: str,
    product_info: str,
    style_text: str,
    golden: str,
    material: str,
    task: str,
    llm: CourseLLM,
    user_id: int,
    previous: str = "",
    instruction: str = "",
    agents_client=None,
    stories_stage: str = "",
    launch_info: str = "",
) -> tuple[str, str, List[str]]:
    """Возвращает (text, model, issues). Issues могут быть, если повтор не помог."""
    from config import config
    from course.stories_cycle import stage_prompt, writer_stories_rules

    spec = get_format(format_id) or get_format("tg_post")
    model = getattr(config, "CONTENT_WRITER_MODEL", "deepseek-v4-flash")
    format_block = format_prompt_block(spec)
    if spec.id == "stories":
        format_block += "\n" + writer_stories_rules(stories_stage)
        format_block += "\n" + stage_prompt(stories_stage)
    product_blob = product_info or ""
    if launch_info:
        product_blob = (product_blob + "\n\n## Запуск\n" + launch_info).strip()
    static = writer_static_prefix(
        expert_info=expert_info,
        product_info=product_blob,
        style_passport=style_text,
        format_block=format_block,
    )
    dynamic = writer_task_block(
        golden=golden,
        material=material,
        task=task,
        previous=previous,
        instruction=instruction,
    )
    messages = [
        {"role": "system", "content": static},
        {"role": "user", "content": dynamic},
    ]

    async def _call(msgs) -> str:
        if agents_client is not None and is_deepseek_model(model):
            return (await agents_client.run_with_messages(
                msgs,
                user_id,
                temperature=0.7,
                max_tokens=2500,
                log_event_type="content_writer",
            )) or ""
        return await llm.complete(
            model=model,
            messages=msgs,
            user_id=user_id,
            temperature=0.7,
            max_tokens=2500,
            request_kind="content_writer",
        )

    text = (await _call(messages) or "").strip()
    issues = check_draft(text, spec, style_text)
    if issues:
        note = "Исправь черновик. Замечания: " + "; ".join(issues)
        text2 = (await _call(messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": note},
        ]) or "").strip()
        if text2:
            text = text2
            issues = check_draft(text, spec, style_text)
    if spec.markup == "telegram_html":
        text = sanitize_telegram_html(text)
    return text, model, issues
