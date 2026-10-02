"""Промпты трёх стадий веб-студии: план поиска, выжимка сырья, генерация."""

from __future__ import annotations

from typing import Sequence

from course.stories_cycle import stage_prompt
from openai_client.content_prompts import WRITER_ROLE

# ── Стадия 1: план поиска по базе ────────────────────────────────────────────

RETRIEVAL_PLANNER_SYSTEM = """Ты планируешь поиск по базе знаний эксперта перед генерацией контента.

Как устроена база (Chroma, два индекса):

1) `chunks` — куски материалов. Метаданные:
   - `source_kind`: lesson_video (запись урока) | practice (Zoom-практика с участниками) |
     broadcast (эфир) | summary (конспект) | slides (слайды) | extra | post (пост эксперта) |
     expert_info | product_info | dialog (переписка в живом чате) | testimonial (отзыв клиента) |
     expert_reply (ответ эксперта в чате)
   - `lesson_key`: «2.4» (модуль.урок), `module_no`
   - `origin`: youtube | vimeo | kinescope | zoom | disk | telegram_group
   - `start_sec` для видео, `page` для документов, `recorded_on`
2) `cards` — карточки идей из этих материалов. Метаданные: `type`
   (idea | quote | story | metaphor | myth | mistake | exercise | question | pain | insight | case),
   `funnel_stage` (reach | warmup | sale), `lesson_key`.

Тебе дают: запрос эксперта, выбранные объекты, формат контента, этап прогрева, фокус и последние реплики диалога.

Верни ТОЛЬКО JSON:
{
  "searches": [
    {"collection":"chunks","text":"семантический запрос","source_kind":["practice"],"lesson_key":"2.4","k":8,"why":"зачем это нужно"},
    {"collection":"cards","text":"семантический запрос","types":["question","pain"],"k":6,"why":"..."}
  ],
  "distill_focus":"одной фразой: что искать в сырых расшифровках выбранных объектов",
  "is_revision": true или false,
  "notes":"что важно учесть при генерации (1–2 предложения)"
}

`is_revision` = true, если эксперт правит ПРЕДЫДУЩИЙ ответ, а не просит новое:
«сделай короче», «жёстче хук», «убери последний кадр», «перепиши третий», «добавь CTA»,
«не нравится, переделай». Тогда материал уже собран на прошлом ходу, новые поиски нужны
только если эксперт просит добавить чего-то, чего в прошлом ответе не было.

Правила:
- 2–4 поиска, разные ракурсы: мысль эксперта, голос аудитории, доказательства.
- Для правки (`is_revision=true`) хватает 0–1 поиска.
- `lesson_key` ставь только если урок явно назван в запросе или в выбранных объектах.
- Просят голос клиентов, отзывы, «как говорят люди» → отдельный поиск по source_kind
  ["testimonial","dialog"] или по cards с types ["question","pain","case"].
- Нужны формулировки и примеры эксперта → source_kind ["lesson_video","broadcast","summary","post"].
- `k` от 4 до 12. Пустые фильтры не пиши.
- `text` — на русском, по смыслу задачи, а не копия запроса.
"""


def planner_user_block(
    *,
    request: str,
    objects: Sequence[str],
    format_title: str,
    stage_title: str,
    focus: str,
    history_tail: Sequence[str],
) -> str:
    parts = [f"Запрос эксперта:\n{request.strip() or '—'}"]
    parts.append("Выбранные объекты: " + (", ".join(objects) if objects else "ничего не выбрано"))
    parts.append(f"Формат контента: {format_title}")
    parts.append(f"Этап прогрева: {stage_title}")
    if (focus or "").strip():
        parts.append(f"Фокус: {focus.strip()}")
    tail = [t for t in history_tail if (t or "").strip()]
    if tail:
        parts.append("Последние реплики:\n" + "\n".join(f"- {t[:300]}" for t in tail[-4:]))
    return "\n\n".join(parts)


# ── Стадия 2: выжимка из сырого материала ───────────────────────────────────

DISTILL_SYSTEM = """Ты читаешь сырую расшифровку материала эксперта и выписываешь ТОЛЬКО то, что нужно под задачу.

Верни ТОЛЬКО JSON:
{
  "excerpts":[{"quote":"дословно 1–4 предложения из куска","start_sec":0,"speaker":"expert|participant","why":"чем полезно для задачи"}],
  "themes":["короткие темы куска, по делу"],
  "audience_voice":["дословные формулировки участников: вопросы, боли, инсайты"]
}

Правила:
- Только дословные цитаты из куска. Ничего не переписывай и не додумывай.
- До 6 выдержек на кусок: самые сильные и самые близкие к задаче.
- start_sec — секунда начала фрагмента из таймкода блока, если он есть, иначе 0.
- Участников обезличивай: без имён, городов, профессий и узнаваемых деталей.
- Организационные реплики («меня слышно?», «запись идёт») пропускай.
- Если в куске нет ничего по задаче — верни пустые списки.
"""


def distill_user_block(
    *,
    task: str,
    distill_focus: str,
    source_label: str,
    chunk_no: int,
    chunks_total: int,
    text: str,
) -> str:
    return (
        f"Задача: {task.strip() or '—'}\n"
        f"Что искать: {distill_focus.strip() or 'сильные мысли и живые формулировки по задаче'}\n"
        f"Материал: {source_label} (кусок {chunk_no}/{chunks_total})\n\n"
        f"{text}"
    )


# ── Стадия 3: генерация ─────────────────────────────────────────────────────

WEB_CHAT_ROLE = (
    WRITER_ROLE
    + """
Ты работаешь в студии эксперта: слева он выбирает объекты (уроки, записи, паспорта, живой чат),
справа — формат, этап прогрева и фокус, посередине — этот диалог.

Как отвечать:
- Просят контент — выдавай готовый материал в заданном формате, без преамбул вроде «конечно, вот».
- Просят поправить прошлый ответ («короче», «жёстче», «убери кадр 3», «переделай») — бери свой
  прошлый ответ из истории диалога и выдавай исправленную версию целиком, тем же форматом.
  Не начинай тему заново и не переспрашивай, о чём речь.
- Просят обсудить, оценить, доработать — отвечай по делу и коротко.
- Материал ниже — единственный источник фактов, цитат, цен и дат. Нет в материале — не утверждай.
- Дословные цитаты бери из выдержек и чанков, не переписывай их.
- Если материала под задачу мало, скажи об этом одной строкой и предложи, какой объект выбрать.
"""
)


def context_block(
    *,
    focus: str,
    stage_id: str,
    objects_summary: str,
    notes: str,
    excerpts: str,
    rag_chunks: str,
    cards: str,
    lesson_passports: str,
    raw_full: str,
    golden: str = "",
) -> str:
    parts: list[str] = ["# Контекст задачи"]
    if (focus or "").strip():
        parts.append(f"## Фокус\n{focus.strip()}")
    parts.append(f"## Этап\n{stage_prompt(stage_id)}")
    if objects_summary:
        parts.append(f"## Выбранные объекты\n{objects_summary}")
    if (notes or "").strip():
        parts.append(f"## На что обратить внимание\n{notes.strip()}")
    if lesson_passports:
        parts.append(f"## Паспорта уроков\n{lesson_passports}")
    if raw_full:
        parts.append(f"## Полная расшифровка выбранного материала\n{raw_full}")
    if excerpts:
        parts.append(f"## Выдержки из сырых материалов (дословно)\n{excerpts}")
    if rag_chunks:
        parts.append(f"## Фрагменты из базы\n{rag_chunks}")
    if cards:
        parts.append(f"## Карточки идей\n{cards}")
    if (golden or "").strip():
        parts.append(
            "## Образцы, которые эксперт уже одобрил (ориентир по тону и структуре, "
            f"не переписывай их дословно)\n{golden.strip()}"
        )
    return "\n\n".join(parts)
