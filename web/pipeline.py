"""Конвейер ответа веб-студии.

Три стадии с разными моделями:

1. **План поиска** (дешёвая модель): по запросу, выбранным объектам, формату, этапу и
   фокусу строит эвристики поиска по базе — какие коллекции, фильтры и запросы.
2. **Выжимка сырья** (дешёвая модель с большим контекстом): читает сырые расшифровки
   выбранных объектов целиком и выписывает дословные выдержки под задачу. Если сырья
   мало, оно уходит в генерацию целиком без выжимки.
3. **Генерация** (сильная модель): паспорта + формат + этап + фокус + выдержки +
   чанки из базы + карточки + история диалога → готовый контент.

Всё, что ушло в модель, возвращается в `trace` — это видно в интерфейсе.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from course.formats import format_prompt_block, get_format
from course.llm import CourseLLM, is_deepseek_model
from course.passports import load_passport
from course.paths import source_dir
from course.products import EXPERT_PRODUCT_ID, active_product_id
from course.speech import segments_from_dicts
from course.stories_cycle import normalize_stage, stage_title, writer_stories_rules
from openai_client.content_prompts import writer_static_prefix
from rag.scope import scope_from_stack
from web.objects import LIVE_FILTERS, LIVE_NAMES, ObjectRef, parse_object_ids, source_label
from web.prompts import (
    DISTILL_SYSTEM,
    RETRIEVAL_PLANNER_SYSTEM,
    WEB_CHAT_ROLE,
    context_block,
    distill_user_block,
    planner_user_block,
)

logger = logging.getLogger(__name__)

_LIVE_KIND_NAMES = LIVE_NAMES


@dataclass
class Search:
    collection: str = "chunks"
    text: str = ""
    source_kind: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)
    lesson_key: str = ""
    k: int = 8
    why: str = ""

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"collection": self.collection, "text": self.text, "k": self.k}
        if self.source_kind:
            out["source_kind"] = self.source_kind
        if self.types:
            out["types"] = self.types
        if self.lesson_key:
            out["lesson_key"] = self.lesson_key
        if self.why:
            out["why"] = self.why
        return out


@dataclass
class TurnResult:
    text: str
    model: str
    trace: Dict[str, Any]


# ── Стадия 1: план поиска ───────────────────────────────────────────────────


def _parse_plan(data: Dict[str, Any]) -> Tuple[List[Search], str, str]:
    searches: List[Search] = []
    for row in (data.get("searches") or [])[:5]:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        coll = "cards" if str(row.get("collection") or "").strip() == "cards" else "chunks"
        try:
            k = int(row.get("k") or 8)
        except (TypeError, ValueError):
            k = 8
        searches.append(
            Search(
                collection=coll,
                text=text[:600],
                source_kind=[str(x) for x in (row.get("source_kind") or []) if str(x).strip()][:6],
                types=[str(x) for x in (row.get("types") or []) if str(x).strip()][:6],
                lesson_key=str(row.get("lesson_key") or "").strip(),
                k=max(3, min(12, k)),
                why=str(row.get("why") or "").strip()[:200],
            )
        )
    return (
        searches,
        str(data.get("distill_focus") or "").strip()[:400],
        str(data.get("notes") or "").strip()[:600],
        bool(data.get("is_revision")),
    )


_REVISION_HINTS = (
    "короч", "длинн", "жёстч", "жестч", "мягч", "перепиш", "переделай", "поправь",
    "убери", "добавь", "замени", "сократи", "расширь", "не нравится", "ещё раз",
    "еще раз", "другой вариант", "вместо",
)


def looks_like_revision(text: str) -> bool:
    """Грубая проверка на правку — страховка, если планировщик не ответил."""
    low = (text or "").strip().casefold()
    if len(low) > 220:
        return False
    return any(h in low for h in _REVISION_HINTS)


def _fallback_plan(user_text: str, refs: Sequence[ObjectRef]) -> Tuple[List[Search], str, str]:
    """Если планировщик не ответил: простой поиск по чанкам и карточкам.

    Срезы живого чата здесь не нужны — они ищутся отдельно, со своими фильтрами.
    """
    searches = [
        Search(collection="chunks", text=user_text[:600], k=8, why="запрос как есть"),
        Search(collection="cards", text=user_text[:600], k=6, why="идеи по теме"),
    ]
    return searches, user_text[:300], ""


async def plan_retrieval(
    llm: CourseLLM,
    *,
    user_text: str,
    refs: Sequence[ObjectRef],
    object_names: Sequence[str],
    format_title: str,
    stage_id: str,
    focus: str,
    history_tail: Sequence[str],
    user_id: int,
) -> Tuple[List[Search], str, str, bool, str]:
    """→ (searches, distill_focus, notes, is_revision, model)"""
    from config import config

    model = str(getattr(config, "WEB_PLANNER_MODEL", "") or "gpt-4o-mini")
    user_block = planner_user_block(
        request=user_text,
        objects=object_names,
        format_title=format_title,
        stage_title=stage_title(stage_id),
        focus=focus,
        history_tail=history_tail,
    )
    try:
        data = await llm.complete_json(
            model=model,
            messages=[
                {"role": "system", "content": RETRIEVAL_PLANNER_SYSTEM},
                {"role": "user", "content": user_block},
            ],
            user_id=user_id,
            temperature=0.2,
            max_tokens=1200,
            request_kind="web_retrieval_plan",
        )
        searches, focus_hint, notes, is_revision = _parse_plan(data or {})
        answered = isinstance(data, dict) and (
            "searches" in data or "is_revision" in data or "distill_focus" in data
        )
        if searches or answered:
            return (
                searches,
                focus_hint or user_text[:300],
                notes,
                is_revision or looks_like_revision(user_text),
                model,
            )
    except Exception as e:
        logger.warning("web plan_retrieval: %s", e)
    searches, focus_hint, notes = _fallback_plan(user_text, refs)
    return searches, focus_hint, notes, looks_like_revision(user_text), f"{model} (fallback)"


# ── Сырьё выбранных объектов ────────────────────────────────────────────────


def _mmss(sec: float) -> str:
    total = int(max(0.0, float(sec or 0)))
    return f"{total // 60}:{total % 60:02d}"


def blocks_from_segments(segments: Sequence[Any], *, block_chars: int = 1200) -> str:
    """Полная расшифровка блоками по ~1200 знаков с таймкодом начала блока.

    Ничего не обрезаем: это сырьё для выжимки и источник дословных цитат.
    """
    out: List[str] = []
    buf: List[str] = []
    start: Optional[float] = None
    size = 0
    for seg in segments:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        if start is None:
            start = float(getattr(seg, "start_sec", 0.0) or 0.0)
        buf.append(text)
        size += len(text) + 1
        if size >= block_chars:
            out.append(f"[{_mmss(start)}] " + " ".join(buf))
            buf, start, size = [], None, 0
    if buf:
        out.append(f"[{_mmss(start or 0.0)}] " + " ".join(buf))
    return "\n\n".join(out)


def raw_text_for_source(source_id: str) -> str:
    """Расшифровка с таймкодами или текст страниц — целиком, без обрезки."""
    dest = source_dir(source_id)
    tr = dest / "transcript.json"
    if tr.is_file():
        try:
            data = json.loads(tr.read_text(encoding="utf-8"))
            segs = segments_from_dicts(data.get("segments") or [])
            if segs:
                return blocks_from_segments(segs)
        except Exception as e:
            logger.warning("raw_text_for_source transcript %s: %s", source_id, e)
    for name in ("pages.json", "posts.json"):
        path = dest / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if name == "posts.json":
                return "\n\n---\n\n".join(str(x) for x in data if str(x).strip())
            return "\n\n".join(
                f"[стр. {p.get('page') or i + 1}]\n{(p.get('text') or '').strip()}"
                for i, p in enumerate(data)
                if (p.get("text") or "").strip()
            )
        except Exception as e:
            logger.warning("raw_text_for_source %s %s: %s", name, source_id, e)
    return ""


def split_for_distill(text: str, *, chunk_chars: int, max_chunks: int) -> List[str]:
    """Режем по пустым строкам, не разрывая блоки; если кусков больше лимита — берём равномерно."""
    blocks = [b for b in (text or "").split("\n\n") if b.strip()]
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for block in blocks:
        add = len(block) + 2
        if buf and size + add > chunk_chars:
            chunks.append("\n\n".join(buf))
            buf, size = [block], len(block)
        else:
            buf.append(block)
            size += add
    if buf:
        chunks.append("\n\n".join(buf))
    if len(chunks) <= max_chunks:
        return chunks
    step = len(chunks) / float(max_chunks)
    return [chunks[min(len(chunks) - 1, int(i * step))] for i in range(max_chunks)]


def _distill_cache_path(source_id: str, key: str) -> Path:
    folder = source_dir(source_id) / "distill"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.json"


def _distill_key(task: str, focus: str) -> str:
    raw = f"{(task or '').strip().casefold()}|{(focus or '').strip().casefold()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _latest_distill(source_id: str) -> Optional[Dict[str, Any]]:
    """Самая свежая выжимка этого источника — для правок прошлого ответа."""
    folder = source_dir(source_id) / "distill"
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("excerpts"):
            data["cached"] = True
            return data
    return None


async def distill_source(
    llm: CourseLLM,
    *,
    source_id: str,
    label: str,
    raw: str,
    task: str,
    distill_focus: str,
    user_id: int,
    prefer_cached: bool = False,
) -> Dict[str, Any]:
    """Дешёвая модель читает весь материал и отдаёт дословные выдержки под задачу.

    ``prefer_cached`` — для правок прошлого ответа: материал уже собран на прошлом
    ходу, гонять модель по всей расшифровке заново незачем.
    """
    from config import config

    model = str(getattr(config, "WEB_DISTILL_MODEL", "") or "gpt-4o-mini")
    key = _distill_key(task, distill_focus)
    cache = _distill_cache_path(source_id, key)
    if prefer_cached and not cache.is_file():
        previous = _latest_distill(source_id)
        if previous:
            previous["reused"] = True
            return previous
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            data["cached"] = True
            return data
        except Exception:
            pass

    chunks = split_for_distill(
        raw,
        chunk_chars=int(getattr(config, "WEB_DISTILL_CHUNK_CHARS", 24_000) or 24_000),
        max_chunks=int(getattr(config, "WEB_DISTILL_MAX_CHUNKS", 12) or 12),
    )
    if not chunks:
        return {"excerpts": [], "themes": [], "audience_voice": [], "chunks": 0, "model": model}

    fallback_model = str(getattr(config, "COURSE_MINING_MODEL", "") or "gpt-4o-mini")
    sem = asyncio.Semaphore(max(1, int(getattr(config, "COURSE_LLM_CONCURRENCY", 3) or 3)))
    used_models: set[str] = set()

    async def _call(model_name: str, i: int, text: str) -> Dict[str, Any]:
        return await llm.complete_json(
            model=model_name,
            messages=[
                {"role": "system", "content": DISTILL_SYSTEM},
                {
                    "role": "user",
                    "content": distill_user_block(
                        task=task,
                        distill_focus=distill_focus,
                        source_label=label,
                        chunk_no=i + 1,
                        chunks_total=len(chunks),
                        text=text,
                    ),
                },
            ],
            user_id=user_id,
            temperature=0.2,
            max_tokens=2500,
            request_kind="web_distill",
        )

    async def _one(i: int, text: str) -> Dict[str, Any]:
        async with sem:
            data: Dict[str, Any] = {}
            try:
                data = await _call(model, i, text)
                used_models.add(model)
            except Exception as e:
                logger.warning("web distill chunk %s/%s (%s): %s", i + 1, len(chunks), model, e)
            if data.get("excerpts"):
                return data
            # Пустой или нечитаемый JSON: добираем куском на запасной модели.
            if fallback_model and fallback_model != model:
                try:
                    data2 = await _call(fallback_model, i, text)
                    used_models.add(fallback_model)
                    if data2.get("excerpts"):
                        logger.info(
                            "web distill chunk %s/%s: %s не ответила, помог %s",
                            i + 1, len(chunks), model, fallback_model,
                        )
                        return data2
                except Exception as e:
                    logger.warning(
                        "web distill chunk %s/%s (%s): %s", i + 1, len(chunks), fallback_model, e
                    )
            return data

    parts = await asyncio.gather(*[_one(i, c) for i, c in enumerate(chunks)])

    excerpts: List[Dict[str, Any]] = []
    themes: List[str] = []
    voices: List[str] = []
    seen: set[str] = set()
    for part in parts:
        for ex in (part or {}).get("excerpts") or []:
            if not isinstance(ex, dict):
                continue
            quote = str(ex.get("quote") or "").strip()
            if len(quote) < 20:
                continue
            fp = quote.casefold()[:120]
            if fp in seen:
                continue
            seen.add(fp)
            try:
                start = float(ex.get("start_sec") or 0)
            except (TypeError, ValueError):
                start = 0.0
            excerpts.append(
                {
                    "quote": quote[:1200],
                    "start_sec": start,
                    "speaker": "participant"
                    if str(ex.get("speaker") or "").strip() == "participant"
                    else "expert",
                    "why": str(ex.get("why") or "").strip()[:200],
                }
            )
        for th in (part or {}).get("themes") or []:
            t = str(th or "").strip()
            if t and t.casefold() not in {x.casefold() for x in themes}:
                themes.append(t[:120])
        for v in (part or {}).get("audience_voice") or []:
            s = str(v or "").strip()
            if s and s.casefold()[:80] not in {x.casefold()[:80] for x in voices}:
                voices.append(s[:400])

    out = {
        "excerpts": excerpts,
        "themes": themes[:20],
        "audience_voice": voices[:20],
        "chunks": len(chunks),
        "model": ", ".join(sorted(used_models)) or model,
        "cached": False,
    }
    if excerpts:
        try:
            cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug("distill cache write: %s", e)
    else:
        logger.warning(
            "web distill: ни одной выдержки (%s, %s кусков, модели: %s)",
            label, len(chunks), out["model"],
        )
    return out


def format_excerpts(by_source: Dict[str, Dict[str, Any]], labels: Dict[str, str]) -> str:
    blocks: List[str] = []
    for sid, data in by_source.items():
        lines: List[str] = [f"### {labels.get(sid, sid)}"]
        themes = data.get("themes") or []
        if themes:
            lines.append("Темы: " + ", ".join(themes[:10]))
        for ex in data.get("excerpts") or []:
            who = "участница" if ex.get("speaker") == "participant" else "эксперт"
            stamp = f" [{_mmss(ex.get('start_sec') or 0)}]" if ex.get("start_sec") else ""
            why = f" — {ex['why']}" if ex.get("why") else ""
            lines.append(f"- ({who}{stamp}) «{ex.get('quote')}»{why}")
        voices = data.get("audience_voice") or []
        if voices:
            lines.append("Голос аудитории:")
            lines.extend(f"- «{v}»" for v in voices[:10])
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ── Сборка материала ────────────────────────────────────────────────────────


async def _lesson_passports(stor, product_id: str, keys: Sequence[str]) -> str:
    out: List[str] = []
    for key in keys:
        les = await stor.get_course_lesson(product_id=product_id, lesson_key=key)
        if not les:
            continue
        text = (les.get("passport_text") or "").strip()
        if text:
            out.append(f"### Урок {key}. {les.get('title') or ''}\n{text[:3000]}")
    return "\n\n".join(out)


async def _cards_block(
    stor, *, product_id: str, source_ids: Sequence[str], lesson_ids: Sequence[int]
) -> str:
    rows: List[Dict[str, Any]] = []
    try:
        for sid in source_ids:
            rows.extend(
                await stor.list_content_cards(
                    product_id=product_id,
                    source_id=UUID(str(sid)),
                    limit=20,
                    order_by="score",
                )
            )
        for lid in lesson_ids:
            rows.extend(
                await stor.list_content_cards(
                    product_id=product_id,
                    lesson_id=int(lid),
                    limit=20,
                    order_by="score",
                )
            )
    except Exception as e:
        logger.warning("web cards_block: %s", e)
    seen: set[str] = set()
    lines: List[str] = []
    for row in sorted(rows, key=lambda r: float(r.get("score") or 0), reverse=True):
        cid = str(row.get("id"))
        if cid in seen:
            continue
        seen.add(cid)
        who = "участница" if row.get("speaker") == "participant" else "эксперт"
        quote = (row.get("quote") or "").strip()
        tail = f" Цитата: «{quote}»" if quote else ""
        lines.append(
            f"- [{row.get('type')}, {who}] {row.get('title')}: {(row.get('text') or '')[:400]}{tail}"
        )
        if len(lines) >= 24:
            break
    return "\n".join(lines)


def _trim(parts: Dict[str, str], budget: int) -> Dict[str, str]:
    """Режем в порядке важности: карточки → чанки → выдержки → сырьё."""
    order = ["golden", "cards", "rag_chunks", "excerpts", "raw_full"]
    total = sum(len(v) for v in parts.values())
    for key in order:
        if total <= budget:
            break
        value = parts.get(key) or ""
        if not value:
            continue
        over = total - budget
        if len(value) <= over:
            parts[key] = ""
            total -= len(value)
        else:
            keep = max(0, len(value) - over - 2)  # 2 знака на «\n…»
            parts[key] = value[:keep].rstrip() + "\n…"
            total = sum(len(v) for v in parts.values())
    return parts


# ── Главный ход ─────────────────────────────────────────────────────────────


async def run_turn(
    app,
    *,
    chat: Dict[str, Any],
    user_text: str,
    history: Sequence[Dict[str, Any]],
    user_id: int,
    object_names: Optional[Dict[str, str]] = None,
    on_stage: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> TurnResult:
    from config import config

    def _stage(name: str, info: Optional[Dict[str, Any]] = None) -> None:
        if on_stage is not None:
            try:
                on_stage(name, info or {})
            except Exception as e:  # pragma: no cover
                logger.debug("on_stage %s: %s", name, e)

    stor = app.user_storage
    product_id = str(chat.get("product_id") or active_product_id())
    ctx = chat.get("context") or {}
    refs = parse_object_ids(ctx.get("objects") or [])
    stage_id = normalize_stage(str(chat.get("stage") or ""))
    focus = str(chat.get("focus") or "")
    fmt = get_format(str(chat.get("format") or "")) or get_format("stories")
    names = object_names or {}
    trace: Dict[str, Any] = {"stages": [], "objects": [], "rag": {}, "mode": "none"}

    # Объекты: источники, уроки, паспорта, живой чат
    source_ids = [r.value for r in refs if r.kind == "source"]
    lesson_keys = [r.value for r in refs if r.kind == "lesson"]
    live_keys = [r.value for r in refs if r.kind == "live"]
    sources: Dict[str, Dict[str, Any]] = {}
    labels: Dict[str, str] = {}
    skipped: List[str] = []
    for sid in source_ids:
        src = await stor.get_course_source(UUID(sid))
        if not src:
            continue
        if str(src.get("status") or "") != "done":
            # Источник удалён или не обработался — брать из него нечего.
            skipped.append(f"{source_label(src)} ({src.get('status')})")
            continue
        sources[sid] = src
        labels[sid] = names.get(f"source:{sid}") or source_label(src)
    lesson_rows = []
    for key in lesson_keys:
        les = await stor.get_course_lesson(product_id=product_id, lesson_key=key)
        if les:
            lesson_rows.append(les)
            for src in await stor.list_course_sources_for_lesson(int(les["id"])):
                sid = str(src["id"])
                if sid not in sources and str(src.get("status")) == "done":
                    sources[sid] = src
                    labels[sid] = source_label(src)
    objects_summary_lines = [f"- {labels[sid]}" for sid in sources]
    objects_summary_lines += [f"- Урок {r['lesson_key']}. {r.get('title') or ''}" for r in lesson_rows]
    objects_summary_lines += [f"- Живой чат: {_LIVE_KIND_NAMES.get(k, k)}" for k in live_keys]
    trace["objects"] = [line[2:] for line in objects_summary_lines]
    if skipped:
        trace["skipped"] = skipped

    llm = CourseLLM(stor)
    history_tail = [str(m.get("text") or "") for m in history[-4:]]

    # ── Стадия 1: план поиска
    _stage("plan", {})
    t0 = time.monotonic()
    searches, distill_focus, notes, is_revision, planner_model = await plan_retrieval(
        llm,
        user_text=user_text,
        refs=refs,
        object_names=trace["objects"],
        format_title=fmt.title,
        stage_id=stage_id,
        focus=focus,
        history_tail=history_tail,
        user_id=user_id,
    )
    trace["stages"].append(
        {
            "name": "plan",
            "model": planner_model,
            "ms": int((time.monotonic() - t0) * 1000),
            "searches": [s.as_dict() for s in searches],
            "distill_focus": distill_focus,
            "notes": notes,
            "is_revision": is_revision,
        }
    )

    # ── Стадия 2а: сырьё выбранных объектов
    raws: Dict[str, str] = {}
    for sid in sources:
        text = raw_text_for_source(sid)
        if text:
            raws[sid] = text
    raw_total = sum(len(v) for v in raws.values())
    inline_limit = int(getattr(config, "WEB_RAW_INLINE_CHARS", 45_000) or 45_000)
    raw_full = ""
    excerpts_text = ""
    if raws and raw_total <= inline_limit:
        trace["mode"] = "raw_inline"
        _stage("raw", {"chars": raw_total, "sources": len(raws)})
        raw_full = "\n\n".join(f"### {labels.get(sid, sid)}\n{raws[sid]}" for sid in raws)
        trace["stages"].append(
            {"name": "raw", "model": "—", "ms": 0, "chars": raw_total, "sources": len(raws)}
        )
    elif raws:
        trace["mode"] = "distill"
        _stage("distill", {"sources": len(raws), "raw_chars": raw_total})
        t1 = time.monotonic()
        results = await asyncio.gather(
            *[
                distill_source(
                    llm,
                    source_id=sid,
                    label=labels.get(sid, sid),
                    raw=raws[sid],
                    task=user_text,
                    distill_focus=distill_focus,
                    user_id=user_id,
                    prefer_cached=is_revision,
                )
                for sid in raws
            ]
        )
        by_source = {sid: res for sid, res in zip(raws.keys(), results)}
        excerpts_text = format_excerpts(by_source, labels)
        trace["stages"].append(
            {
                "name": "distill",
                "model": ", ".join(
                    sorted({str((r or {}).get("model") or "") for r in results if r})
                )
                or str(getattr(config, "WEB_DISTILL_MODEL", "")),
                "ms": int((time.monotonic() - t1) * 1000),
                "sources": len(raws),
                "raw_chars": raw_total,
                "llm_chunks": sum(int((r or {}).get("chunks") or 0) for r in results),
                "excerpts": sum(len((r or {}).get("excerpts") or []) for r in results),
                "cached": all(bool((r or {}).get("cached")) for r in results) if results else False,
                "reused": any(bool((r or {}).get("reused")) for r in results),
            }
        )

    # ── Стадия 2б: поиск по базе
    _stage("search", {"searches": len(searches)})
    rag_chunks_text = ""
    golden_text = ""
    golden_hits: List[Dict[str, Any]] = []
    cards_from_rag: List[str] = []
    gw = None
    if getattr(app, "rag_stack", None):
        try:
            gw = scope_from_stack(app.rag_stack)
        except Exception as e:
            logger.warning("web rag scope: %s", e)
    if gw is not None:
        chunk_hits: List[Dict[str, Any]] = []
        max_chunks = int(getattr(config, "WEB_RAG_MAX_CHUNKS", 14) or 14)
        # Выбранные срезы живого чата ищем всегда: эксперт отметил их сам.
        for key in live_keys:
            flt = dict(LIVE_FILTERS.get(key) or {})
            kinds = [flt.pop("source_kind")] if "source_kind" in flt else None
            try:
                chunk_hits.extend(
                    gw.search_chunks(user_text, k=8, kinds=kinds, **flt)
                )
            except Exception as e:
                logger.warning("web live search %s: %s", key, e)
        for s in searches:
            try:
                if s.collection == "cards":
                    hits = gw.search_cards(
                        s.text, k=s.k, lesson_key=s.lesson_key or None, types=s.types or None
                    )
                    for h in hits:
                        doc = str(h.get("document") or "").strip()
                        if doc:
                            cards_from_rag.append(f"- {doc[:500]}")
                else:
                    hits = gw.search_chunks(
                        s.text,
                        k=s.k,
                        lesson_key=s.lesson_key or None,
                        kinds=s.source_kind or None,
                    )
                    chunk_hits.extend(hits)
            except Exception as e:
                logger.warning("web search %s: %s", s.as_dict(), e)
        # чанки уже выбранных объектов не дублируем: их сырьё и так в контексте
        if raws:
            chunk_hits = [
                h
                for h in chunk_hits
                if str((h.get("metadata") or {}).get("source_id") or "") not in raws
            ]
        seen_ids: set[str] = set()
        unique: List[Dict[str, Any]] = []
        for h in sorted(chunk_hits, key=lambda x: float(x.get("distance") or 0)):
            hid = str(h.get("id"))
            if hid in seen_ids:
                continue
            seen_ids.add(hid)
            unique.append(h)
            if len(unique) >= max_chunks:
                break
        rag_chunks_text = gw.format_chunks_for_prompt(unique, max_per_source=2)
        try:
            golden_hits = gw.golden_examples(user_text, fmt.id, k=2)
            golden_text = "\n\n---\n\n".join(
                str((h.get("metadata") or {}).get("answer") or "").strip()
                for h in golden_hits
                if str((h.get("metadata") or {}).get("answer") or "").strip()
            )[:12_000]
        except Exception as e:
            logger.warning("web golden: %s", e)
        trace["rag"] = {
            "chunks": len(unique),
            "cards": len(cards_from_rag),
            "golden": len(golden_hits),
        }

    # ── Материал из БД: паспорта уроков и карточки
    lesson_passports = await _lesson_passports(stor, product_id, lesson_keys)
    cards_db = await _cards_block(
        stor,
        product_id=product_id,
        source_ids=list(sources.keys()),
        lesson_ids=[int(r["id"]) for r in lesson_rows],
    )
    cards_text = "\n".join([x for x in [cards_db, "\n".join(cards_from_rag)] if x.strip()])

    parts = _trim(
        {
            "raw_full": raw_full,
            "excerpts": excerpts_text,
            "rag_chunks": rag_chunks_text,
            "cards": cards_text,
            "golden": golden_text,
        },
        int(getattr(config, "WEB_CONTEXT_MAX_CHARS", 120_000) or 120_000),
    )

    # ── Стадия 3: генерация
    expert_p = await load_passport(stor, "expert")
    product_p = await load_passport(stor, "product")
    launch_p = await load_passport(stor, "launch")
    style = await stor.get_active_style_profile(product_id)
    info_max = int(getattr(config, "COURSE_INFO_MAX_CHARS", 8000) or 8000)
    product_blob = (product_p.get("text") or "")[:info_max]
    launch_text = (launch_p.get("text") or "")[:info_max]
    if launch_text:
        product_blob = (product_blob + "\n\n## Запуск\n" + launch_text).strip()

    format_block = format_prompt_block(fmt)
    if fmt.id == "stories":
        format_block += "\n" + writer_stories_rules(stage_id)

    static_prefix = WEB_CHAT_ROLE + "\n\n" + writer_static_prefix(
        expert_info=(expert_p.get("text") or "")[:info_max],
        product_info=product_blob,
        style_passport=(style or {}).get("text") or "",
        format_block=format_block,
    )
    ctx_block = context_block(
        focus=focus,
        stage_id=stage_id,
        objects_summary="\n".join(objects_summary_lines),
        notes=notes,
        excerpts=parts.get("excerpts") or "",
        rag_chunks=parts.get("rag_chunks") or "",
        cards=parts.get("cards") or "",
        lesson_passports=lesson_passports,
        raw_full=parts.get("raw_full") or "",
        golden=parts.get("golden") or "",
    )

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": static_prefix},
        {"role": "system", "content": ctx_block},
    ]
    history_budget = int(getattr(config, "WEB_HISTORY_MAX_CHARS", 40_000) or 40_000)
    picked: List[Dict[str, str]] = []
    used = 0
    for row in reversed(list(history)):  # с конца: свежие реплики важнее
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        if used + len(text) > history_budget and picked:
            break
        role = "assistant" if str(row.get("role")) == "assistant" else "user"
        picked.append({"role": role, "content": text})
        used += len(text)
    picked.reverse()
    messages.extend(picked)
    messages.append({"role": "user", "content": user_text})

    writer_model = str(
        getattr(config, "WEB_WRITER_MODEL", "")
        or getattr(config, "CONTENT_WRITER_MODEL", "")
        or "deepseek-v4-flash"
    )
    max_tokens = int(getattr(config, "CONTENT_WRITER_MAX_TOKENS", 16000) or 16000)
    messaging = app.feature_manager.get_optional("messaging")
    agents = getattr(messaging, "agents_client", None) if messaging else None

    _stage("write", {"context_chars": len(ctx_block), "model": writer_model})
    t2 = time.monotonic()
    if agents is not None and is_deepseek_model(writer_model):
        text = (
            await agents.run_with_messages(
                messages,
                user_id,
                temperature=0.7,
                max_tokens=max_tokens,
                log_event_type="web_studio",
            )
            or ""
        )
    else:
        text = await llm.complete(
            model=writer_model,
            messages=messages,
            user_id=user_id,
            temperature=0.7,
            max_tokens=max_tokens,
            request_kind="web_studio",
        )
    trace["stages"].append(
        {
            "name": "write",
            "model": writer_model,
            "ms": int((time.monotonic() - t2) * 1000),
            "context_chars": len(ctx_block),
            "prefix_chars": len(static_prefix),
            "history": len(picked),
            "history_chars": used,
        }
    )
    trace["context_chars"] = len(ctx_block)
    return TurnResult(text=(text or "").strip(), model=writer_model, trace=trace)
