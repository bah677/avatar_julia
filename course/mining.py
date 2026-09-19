"""Разбор источника в карточки (map-reduce) и паспорт урока."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from course.llm import CourseLLM, parse_json_obj
from course.speech import SpeechSegment, format_expert_blocks_for_prompt, merge_segments_window
from openai_client.content_prompts import MINING_SYSTEM, PASSPORT_SYSTEM, mining_system

logger = logging.getLogger(__name__)

_CHUNK_CHARS = 20_000
_CARD_TYPES = {
    "idea", "quote", "story", "metaphor", "myth", "mistake",
    "exercise", "question", "pain", "insight", "case",
}


def _norm_text(s: str) -> str:
    t = (s or "").casefold().replace("ё", "е")
    t = re.sub(r"[^\w\s]+", " ", t, flags=re.U)
    return re.sub(r"\s+", " ", t).strip()


def _split_blocks(blocks_text: str, *, max_chars: int = _CHUNK_CHARS) -> List[str]:
    parts = [p.strip() for p in (blocks_text or "").split("\n\n") if p.strip()]
    if not parts:
        return [blocks_text[:max_chars]] if blocks_text else []
    chunks: List[str] = []
    buf: List[str] = []
    n = 0
    for p in parts:
        add = len(p) + 2
        if buf and n + add > max_chars:
            chunks.append("\n\n".join(buf))
            buf = [p]
            n = len(p)
        else:
            buf.append(p)
            n += add
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _cards_similar(a: dict, b: dict) -> bool:
    ta = _norm_text(a.get("title") or "")
    tb = _norm_text(b.get("title") or "")
    if ta and tb and (ta in tb or tb in ta):
        return True
    try:
        aa = float(a.get("anchor_sec") or 0)
        ba = float(b.get("anchor_sec") or 0)
    except (TypeError, ValueError):
        aa = ba = 0
    if aa and ba and abs(aa - ba) < 25 and ta[:24] == tb[:24]:
        return True
    return False


def _merge_cards(pool: Sequence[dict], *, top_n: int) -> List[dict]:
    ranked = sorted(pool, key=lambda x: float(x.get("score") or 0), reverse=True)
    out: List[dict] = []
    for card in ranked:
        if any(_cards_similar(card, x) for x in out):
            continue
        out.append(card)
        if len(out) >= top_n:
            break
    return out


def _verbatim_ok(quote: str, chunk: str) -> bool:
    q = _norm_text(quote)
    if len(q) < 12:
        return bool(q) and q in _norm_text(chunk)
    return q in _norm_text(chunk)


def _parse_cards(raw: dict, chunk: str) -> List[dict]:
    out: List[dict] = []
    for item in raw.get("cards") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:80]
        text = str(item.get("text") or "").strip()
        if not title or not text:
            continue
        ctype = str(item.get("type") or "idea").strip()
        if ctype not in _CARD_TYPES:
            ctype = "idea"
        quote = str(item.get("quote") or "").strip()
        if quote and not _verbatim_ok(quote, chunk):
            quote = ""
        try:
            anchor = float(item.get("anchor_sec")) if item.get("anchor_sec") not in (None, "") else None
        except (TypeError, ValueError):
            anchor = None
        try:
            page = int(item.get("page")) if item.get("page") not in (None, "") else None
        except (TypeError, ValueError):
            page = None
        speaker = str(item.get("speaker") or "expert").strip()
        if speaker not in ("expert", "participant"):
            speaker = "expert"
        formats = item.get("formats") or []
        if not isinstance(formats, list):
            formats = []
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        out.append(
            {
                "type": ctype,
                "title": title,
                "text": text,
                "quote": quote,
                "anchor_sec": anchor,
                "page": page,
                "speaker": speaker,
                "audience_pain": str(item.get("audience_pain") or "").strip()[:300],
                "funnel_stage": str(item.get("funnel_stage") or "warmup"),
                "formats": [str(x) for x in formats][:6],
                "score": max(0.0, min(100.0, score)),
            }
        )
    return out


async def mine_source(
    *,
    kind: str,
    title: str,
    chunks: Sequence[str],
    llm: CourseLLM,
    user_id: int,
    duration_sec: Optional[int] = None,
    is_document: bool = False,
) -> List[dict]:
    from config import config

    expert = getattr(config, "EXPERT_NAME", "") or "эксперт"
    model = getattr(config, "COURSE_MINING_MODEL", "gpt-4o-mini")
    conc = int(getattr(config, "COURSE_LLM_CONCURRENCY", 3) or 3)
    sem = asyncio.Semaphore(max(1, conc))
    system = mining_system(expert, kind)

    async def _one(i: int, chunk: str) -> List[dict]:
        async with sem:
            user = (
                f"Тип источника: {kind}\nНазвание: {title}\n"
                f"Кусок {i}/{len(chunks)}:\n\n{chunk}"
            )
            try:
                data = await llm.complete_json(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    user_id=user_id,
                    temperature=0.3,
                    max_tokens=3500,
                    request_kind="course_mining",
                )
                return _parse_cards(data, chunk)
            except Exception as e:
                logger.warning("mining chunk %s: %s", i, e)
                return []

    parts = await asyncio.gather(*[_one(i + 1, c) for i, c in enumerate(chunks)])
    pool: List[dict] = []
    for p in parts:
        pool.extend(p)
    per_hour = int(getattr(config, "COURSE_CARDS_PER_HOUR", 30) or 30)
    if is_document:
        top_n = 15
    else:
        hours = max(0.3, (duration_sec or 1800) / 3600.0)
        top_n = max(8, min(80, int(per_hour * hours)))
    return _merge_cards(pool, top_n=top_n)


def mining_chunks_from_segments(segments: Sequence[SpeechSegment]) -> List[str]:
    blocks = format_expert_blocks_for_prompt(
        segments, gap_sec=12.0, limit_blocks=400, max_text_per_block=700
    )
    return _split_blocks(blocks) or ([blocks] if blocks else [])


def mining_chunks_from_pages(pages: Sequence[dict]) -> List[str]:
    parts = []
    for p in pages:
        t = (p.get("text") or "").strip()
        if t:
            parts.append(f"[стр. {p.get('page')}]\n{t}")
    joined = "\n\n".join(parts)
    return _split_blocks(joined) or ([joined] if joined else [])


def window_for_card(
    segments: Sequence[SpeechSegment],
    card: dict,
    *,
    pad_sec: float = 60.0,
) -> str:
    anchor = float(card.get("anchor_sec") or 0)
    start = max(0.0, anchor - pad_sec)
    end = anchor + pad_sec
    if anchor <= 0 and card.get("quote"):
        q = _norm_text(card["quote"])[:40]
        for s in segments:
            if q and q in _norm_text(s.text):
                start = max(0.0, s.start_sec - pad_sec)
                end = s.end_sec + pad_sec
                break
    win = merge_segments_window(list(segments), start, end)
    text = format_expert_blocks_for_prompt(
        win, gap_sec=8.0, limit_blocks=40, max_text_per_block=700
    )
    return text.strip() or (card.get("quote") or card.get("text") or "")


async def build_lesson_passport(
    *,
    lesson_title: str,
    lesson_key: str,
    summary_text: str,
    slides_text: str,
    cards: Sequence[dict],
    video_links: Sequence[str],
    llm: CourseLLM,
    user_id: int,
) -> tuple[dict, str]:
    from config import config

    model = getattr(config, "COURSE_MINING_MODEL", "gpt-4o-mini")
    card_lines = "\n".join(
        f"- {c.get('title')}: {c.get('text')}" for c in list(cards)[:40]
    )
    user = (
        f"Урок {lesson_key} «{lesson_title}»\n\n"
        f"Конспект:\n{(summary_text or '')[:12000]}\n\n"
        f"Слайды:\n{(slides_text or '')[:8000]}\n\n"
        f"Карточки:\n{card_lines}\n\n"
        f"Ссылки на видео:\n" + "\n".join(video_links or [])
    )
    data = await llm.complete_json(
        model=model,
        messages=[
            {"role": "system", "content": PASSPORT_SYSTEM},
            {"role": "user", "content": user},
        ],
        user_id=user_id,
        temperature=0.2,
        max_tokens=2500,
        request_kind="course_passport",
    )
    text = render_passport_text(lesson_key, lesson_title, data, video_links)
    return data, text


def render_passport_text(
    lesson_key: str, title: str, data: dict, video_links: Sequence[str]
) -> str:
    def _list(key: str) -> str:
        items = data.get(key) or []
        if not items:
            return "—"
        if key == "terms":
            return "\n".join(
                f"- {x.get('term')}: {x.get('definition')}"
                for x in items if isinstance(x, dict)
            ) or "—"
        return "\n".join(f"- {x}" for x in items)

    links = list(video_links or data.get("video_links") or [])
    return (
        f"# Урок {lesson_key} «{title}»\n\n"
        f"## Главная мысль\n{data.get('main_idea') or '—'}\n\n"
        f"## Ключевые идеи\n{_list('key_ideas')}\n\n"
        f"## Термины и модели\n{_list('terms')}\n\n"
        f"## Упражнения\n{_list('exercises')}\n\n"
        f"## Истории и метафоры\n{_list('stories')}\n\n"
        f"## Ошибки и мифы\n{_list('mistakes_myths')}\n\n"
        f"## Боли аудитории\n{_list('audience_pains')}\n\n"
        f"## Результат после урока\n{data.get('outcome') or '—'}\n\n"
        f"## Видео\n" + ("\n".join(f"- {x}" for x in links) if links else "—")
    )


async def find_duplicate_card(
    rag_scope,
    card: dict,
    *,
    threshold: float,
) -> Optional[dict]:
    if card.get("type") not in ("question", "pain", "insight"):
        return None
    q = f"{card.get('title')} {card.get('text')}"
    hits = rag_scope.search_cards(q, k=3, types=[card.get("type")])
    for h in hits:
        dist = h.get("distance")
        # cosine collection: distance = 1-cos; L2 squared: cos = 1 - d/2
        sim = None
        if dist is not None:
            d = float(dist)
            if d <= 1.5:
                sim = 1.0 - d  # cosine distance in chroma
            else:
                sim = 1.0 - d / 2.0
        if sim is not None and sim >= threshold:
            return h
    return None
