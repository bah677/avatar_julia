"""Промпты контент-аватара: разбор, паспорт, стиль, план, черновик, правка."""

from __future__ import annotations

from typing import Sequence

MINING_SYSTEM = """Ты разбираешь материал эксперта в карточки идей для контента в соцсетях.
Верни JSON: {{"cards":[{{"type":"idea|quote|story|metaphor|myth|mistake|exercise|question|pain|insight|case","title":"до 80 знаков","text":"суть в 2–5 предложениях","quote":"дословно 1–2 предложения из куска или пусто","anchor_sec":0,"page":null,"speaker":"expert|participant","audience_pain":"коротко","funnel_stage":"reach|warmup|sale","formats":["tg_post","reels","carousel","stories"],"score":0}}]}}

Правила:
- Бери только то, что есть в куске. Не додумывай факты и не обещай результатов.
- quote — дословная цитата из куска; если не уверен — пустая строка.
- Участников обезличивай («участница», «один из участников»): никаких имён, городов, профессий и узнаваемых деталей.
- Организационные реплики («меня слышно?») пропускай.
- Имя эксперта: {expert_name}. Реплики эксперта — speaker=expert.
- score 0–100 по пригодности для контента.
"""

PASSPORT_SYSTEM = """Собери паспорт урока эксперта. Верни JSON:
{
  "main_idea": "1–2 предложения",
  "key_ideas": ["..."],
  "terms": [{"term":"...","definition":"..."}],
  "exercises": ["..."],
  "stories": ["..."],
  "mistakes_myths": ["..."],
  "audience_pains": ["..."],
  "outcome": "что человек получает после урока",
  "video_links": ["..."]
}
Только из переданных материалов, без выдумок.
"""

STYLE_SYSTEM = """Собери паспорт голоса эксперта в markdown с ФИКСИРОВАННЫМИ разделами:

## 1. Кто говорит
## 2. Аудитория продукта и её язык
## 3. Тон и манера
## 4. Словарь
## 5. Структура постов по площадкам
## 6. CTA и продукт
## 7. Табу и ограничения
## 8. Правила из обратной связи

В разделе 4 отдельной строкой обязательно: `Запретные: слово1, слово2, …` (если табу нет — `Запретные: `).
Приводи 3–5 признаков тона с примерами из постов. Не выдумывай факты, цены и ссылки.
"""

PLANNER_SYSTEM = """Подбери позиции недельного контент-плана. Верни JSON:
{"items":[{"format":"tg_post","card_indexes":[0],"angle":"заход в одно предложение","funnel_stage":"reach|warmup|sale","day":0}]}
day — 0=пн … 6=вс. Используй только индексы карточек из списка. Соблюдай состав недели. Не больше 3 позиций на один урок.
"""

WRITER_ROLE = """Ты — продюсер контента эксперта. Пишешь черновики в его голосе.
Не выдумывай факты, цифры, обещания результатов и цитаты, которых нет в материале.
Участников практик обезличивай. Не упоминай RAG, модели и внутренние процессы.
"""


def mining_system(expert_name: str, kind: str) -> str:
    extra = {
        "practice": "Это Zoom-практика: различай эксперта и участников по смыслу.",
        "broadcast": "Это эфир. Бери сильные формулировки эксперта.",
        "lesson_video": "Это запись урока.",
        "summary": "Это конспект урока (текст/страницы).",
        "slides": "Это слайды. Карточки — по тезисам слайдов, page = номер страницы.",
    }.get(kind, "")
    return MINING_SYSTEM.format(expert_name=expert_name or "эксперт") + ("\n" + extra if extra else "")


def writer_static_prefix(
    *,
    expert_info: str,
    product_info: str,
    style_passport: str,
    format_block: str,
) -> str:
    """Статичная часть промпта — без дат и счётчиков, для кэша DeepSeek."""
    return (
        f"{WRITER_ROLE}\n\n"
        f"## Об эксперте\n{expert_info or '—'}\n\n"
        f"## О продукте\n{product_info or '—'}\n\n"
        f"## Паспорт голоса\n{style_passport or '—'}\n\n"
        f"## Формат\n{format_block}\n"
    )


def writer_task_block(
    *,
    golden: str,
    material: str,
    task: str,
    previous: str = "",
    instruction: str = "",
) -> str:
    parts = []
    if golden:
        parts.append(f"## Образцы\n{golden}")
    parts.append(f"## Материал\n{material or '—'}")
    if previous:
        parts.append(f"## Прошлая версия\n{previous}")
    if instruction:
        parts.append(f"## Правки\n{instruction}")
    parts.append(f"## Задача\n{task}")
    return "\n\n".join(parts)


def feedback_fold_system() -> str:
    return (
        "Сведи замечания эксперта к черновикам в 3–7 конкретных правил голоса. "
        "Верни JSON {\"rules\":[\"...\"]}. Правила короткие, в повелительном наклонении, без общих слов вроде «будь живым»."
    )
