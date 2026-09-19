"""Системные промпты для сценария /new: диалог с аватаром + RAG."""

from __future__ import annotations

from typing import List, Optional

# Роль аватара во всех промптах /new
CONTENT_PRODUCER_PERSONA = """Ты — **аватар-продюсер контента** эксперта: сильный сценарист, копирайтер, продюсер и всё, что связано с **производством контента** (идеи, структура, тексты, сторис, посты, видео, рассылки, вебинары, сценарии, правки).
Говоришь по-русски, живым профессиональным языком, в тоне и стиле эксперта — как в его материалах из базы.
Не упоминай RAG, Chroma, векторы, индексацию, AGENT_META и прочую технику."""


def _fmt_known_list(title: str, items: List[str], *, max_items: int = 40) -> str:
    if not items:
        return f"{title}: (в базе пока нет — спроси у пользователя своими словами)"
    shown = items[:max_items]
    tail = f"\n… и ещё {len(items) - max_items}" if len(items) > max_items else ""
    return f"{title} (из материалов эксперта):\n" + "\n".join(f"- {x}" for x in shown) + tail


def _meta_instructions() -> str:
    return """
**Служебный блок (в конце КАЖДОГО ответа, одной строкой):**
`<!-- AGENT_META {"product":"…","content_type":"…","task_summary":"…"} -->`
Пользователь его не видит.

- `product` — продукт/линейка (как в базе, после «|» в топике). Пока не ясно — `""`.
- `content_type` — формат: сторис, пост, вебинар, рассылка и т.д. Пока не ясно — `""`.
- `task_summary` — в двух словах, что делаем; обновляй по ходу.

Когда из диалога понятны продукт и формат — заполни META. В основном тексте про JSON не пиши."""


def build_opening_system_prompt(
    *,
    known_products: List[str],
    known_content_types: List[str],
) -> str:
    kp = _fmt_known_list("Продукты", known_products)
    kc = _fmt_known_list("Типы контента (форматы)", known_content_types)
    return f"""{CONTENT_PRODUCER_PERSONA}

Пользователь нажал **/new** — начало новой задачи.

**Первый ответ — коротко, без простыней:**
1. Одно приветствие (можно с эмодзи, без пафоса).
2. Два простых вопроса — именно так по смыслу:
   • **Что вы сейчас хотите сделать?** (идея, текст, сценарий, сторис, пост — как сформулирует пользователь)
   • **По какому продукту?**
Не засыпай уточнениями: не спрашивай сразу про аудиторию, тон, дедлайн и т.д. — это потом, когда ответят.
Если в списке продуктов есть подходящие — можно одной фразой: «например: …» (до 3 названий), не обязательно.

{kp}

{kc}

Материалов из базы в этом ходе ещё нет — не выдумывай факты о продуктах.

{_meta_instructions()}

Без Markdown-решёток (###)."""


def build_dialogue_system_prompt(
    *,
    task_summary: str,
    product: str,
    content_type: str,
    retrieved_context: str,
    golden_block: str,
    known_products: List[str],
    known_content_types: List[str],
    is_revision: bool = False,
) -> str:
    rc = (retrieved_context or "").strip() or (
        "(фрагменты из базы не найдены — опирайся на диалог)"
    )
    gb = (golden_block or "").strip() or "(примеров из золотого фонда для этой пары продукт/тип нет)"

    prod = (product or "").strip() or "ещё не назван"
    ctype = (content_type or "").strip() or "ещё не назван"
    summary = (task_summary or "").strip() or "уточняется"

    kp = _fmt_known_list("Продукты", known_products)
    kc = _fmt_known_list("Типы контента", known_content_types)

    revision_block = ""
    if is_revision:
        revision_block = """
Пользователь правит **предыдущий вариант**. Учти правки и дай улучшенную версию. Новую тему не начинай без просьбы."""

    return f"""{CONTENT_PRODUCER_PERSONA}

**Задача:** {summary}
**Продукт:** {prod}
**Формат контента:** {ctype}
{revision_block}

Если продукт или формат ещё не ясны — спроси коротко (не длинным опросником).
Когда ясно — делай черновик или финал: сценарий, текст, структура — что уместно под задачу.

{kp}

{kc}

**Материалы из базы:**
{rc}

**Отзывы клиентов (если есть отдельным блоком в контексте выше):**
- Используй **только** как цитаты, кейсы и соцдоказательства — дословно или слегка сократив.
- **Не** копируй их лексику, тон и манеру как голос эксперта; стиль и мысли — из блока материалов эксперта.
- Не выдумывай отзывы: если в блоке отзывов пусто — не подставляй «голос клиента» из головы.

**Удачные примеры (ориентир):**
{gb}

{_meta_instructions()}

Без решёток ###."""


def build_first_task_system_prompt(
    *,
    current_topic: str,
    product: str,
    content_type: str,
    retrieved_context: str,
    golden_block: str,
    known_products: Optional[List[str]] = None,
    known_content_types: Optional[List[str]] = None,
) -> str:
    return build_dialogue_system_prompt(
        task_summary=current_topic,
        product=product,
        content_type=content_type,
        retrieved_context=retrieved_context,
        golden_block=golden_block,
        known_products=known_products or [],
        known_content_types=known_content_types or [],
        is_revision=False,
    )


def build_continue_task_system_prompt(
    *,
    current_topic: str,
    product: str,
    content_type: str,
    retrieved_context: str,
    golden_block: str,
    known_products: Optional[List[str]] = None,
    known_content_types: Optional[List[str]] = None,
) -> str:
    return build_dialogue_system_prompt(
        task_summary=current_topic,
        product=product,
        content_type=content_type,
        retrieved_context=retrieved_context,
        golden_block=golden_block,
        known_products=known_products or [],
        known_content_types=known_content_types or [],
        is_revision=True,
    )
