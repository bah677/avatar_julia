"""Описания форматов контента."""

from __future__ import annotations

from typing import Dict, Optional

from course.models import FormatSpec

FORMATS: Dict[str, FormatSpec] = {
    "tg_post": FormatSpec(
        id="tg_post",
        platform="telegram",
        title="Пост в Telegram",
        min_chars=800,
        max_chars=1500,
        markup="telegram_html",
        structure="Первая строка — крючок. 3–5 абзацев. Вывод. CTA.",
        extra_rules="Разметка Telegram-HTML: только <b>, <i>, <a href>. Без markdown.",
    ),
    "ig_post": FormatSpec(
        id="ig_post",
        platform="instagram",
        title="Подпись Instagram",
        min_chars=0,
        max_chars=2200,
        markup="plain",
        structure="Первая строка — крючок. Абзацы. CTA. До 5 хэштегов в конце.",
        extra_rules="Без HTML и markdown.",
    ),
    "carousel": FormatSpec(
        id="carousel",
        platform="instagram",
        title="Карусель",
        min_chars=0,
        max_chars=4000,
        markup="plain",
        structure=(
            "6–10 слайдов, не больше 25 слов на слайд. "
            "Первый — крючок, последний — CTA. Затем подпись к посту. "
            "В конце номера подходящих слайдов курса, если есть."
        ),
    ),
    "reels": FormatSpec(
        id="reels",
        platform="instagram",
        title="Сценарий Reels/Shorts",
        min_chars=200,
        max_chars=2500,
        markup="plain",
        structure=(
            "Хук не длиннее 3 секунд. 3–5 реплик. CTA. "
            "Текст на экране. Подпись."
        ),
    ),
    "stories": FormatSpec(
        id="stories",
        platform="instagram",
        title="Серия сторис",
        min_chars=150,
        max_chars=4000,
        markup="plain",
        structure=(
            "Лента на день: 8–12 кадров. На каждый кадр — текст на экране (коротко), "
            "реплика голосом, стикер если нужен, роль кадра."
        ),
        extra_rules=(
            "Не простыня и не один пост. CTA не в каждом кадре. "
            "Интерактив (опрос, вопрос, слайдер, квиз) хотя бы раз за ленту. "
            "Опирайся на этап сторис и паспорт запуска, если они заданы."
        ),
    ),
}


def get_format(format_id: str) -> Optional[FormatSpec]:
    return FORMATS.get((format_id or "").strip())


def format_prompt_block(spec: FormatSpec) -> str:
    length = ""
    if spec.min_chars and spec.max_chars:
        length = f"Длина: {spec.min_chars}–{spec.max_chars} знаков."
    elif spec.max_chars:
        length = f"Длина: до {spec.max_chars} знаков."
    return (
        f"Формат: {spec.title} (`{spec.id}`), площадка {spec.platform}.\n"
        f"{length}\n"
        f"Структура: {spec.structure}\n"
        f"{spec.extra_rules}".strip()
    )
