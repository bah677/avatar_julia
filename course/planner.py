"""План недели: отбор карточек и LLM-раскладка."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from course.llm import CourseLLM
from openai_client.content_prompts import PLANNER_SYSTEM

logger = logging.getLogger(__name__)


def week_start_for(now: date, weekday: int = 0) -> date:
    """Понедельник (weekday=0) текущей или следующей недели."""
    delta = (weekday - now.weekday()) % 7
    return now + timedelta(days=delta)


def seconds_until(hour: int, minute: int, *, weekday: int, tz_name: str) -> float:
    from datetime import datetime, timedelta

    tz = ZoneInfo(tz_name or "Europe/Moscow")
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (weekday - now.weekday()) % 7
    target = target + timedelta(days=days_ahead)
    if target <= now:
        target = target + timedelta(days=7)
    return max(5.0, (target - now).total_seconds())


async def build_week_plan(
    *,
    cards: List[dict],
    mix: Dict[str, int],
    focus: str,
    llm: CourseLLM,
    user_id: int,
) -> List[dict]:
    from config import config

    model = getattr(config, "COURSE_PLANNER_MODEL", "gpt-4o-mini")
    mix_line = ", ".join(f"{k}:{v}" for k, v in mix.items() if v)
    lines = []
    for i, c in enumerate(cards):
        lines.append(
            f"{i}. [{c.get('type')}] урок {c.get('lesson_key') or '—'} "
            f"freq={c.get('frequency')} score={c.get('score')}: {c.get('title')} — {c.get('text')[:180]}"
        )
    user = (
        f"Состав недели: {mix_line}\n"
        f"Фокус: {focus or 'не задан'}\n"
        f"Карточки:\n" + "\n".join(lines)
    )
    data = await llm.complete_json(
        model=model,
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": user},
        ],
        user_id=user_id,
        temperature=0.4,
        max_tokens=2000,
        request_kind="course_planner",
    )
    items = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        fmt = str(raw.get("format") or "").strip()
        if fmt not in mix:
            continue
        idxs = raw.get("card_indexes") or raw.get("cards") or []
        card_ids = []
        for ix in idxs[:3]:
            try:
                i = int(ix)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(cards):
                card_ids.append(cards[i]["id"])
        if not card_ids:
            continue
        try:
            day = int(raw.get("day"))
            if day < 0 or day > 6:
                day = None
        except (TypeError, ValueError):
            day = None
        items.append(
            {
                "format": fmt,
                "card_ids": card_ids,
                "angle": str(raw.get("angle") or "").strip()[:300],
                "funnel_stage": str(raw.get("funnel_stage") or "warmup"),
                "planned_day": day,
                "lesson_id": cards[int(idxs[0])].get("lesson_id") if idxs else None,
            }
        )
    # trim to mix counts
    used = {k: 0 for k in mix}
    trimmed = []
    for it in items:
        f = it["format"]
        if used.get(f, 0) >= mix.get(f, 0):
            continue
        used[f] = used.get(f, 0) + 1
        trimmed.append(it)
    return trimmed
