"""Определение урока и типа для ссылок на видео (правила + LLM)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import List, Optional, Sequence

from course.disk_layout import parse_date_from_name, parse_lesson_key_from_text
from course.llm import CourseLLM
from course.models import ClassifyResult, VideoProbe

logger = logging.getLogger(__name__)

_PRACTICE_RE = re.compile(r"практик|zoom|разбор|встреч", re.IGNORECASE)
_BROADCAST_RE = re.compile(r"эфир", re.IGNORECASE)
_LESSON_RE = re.compile(r"урок", re.IGNORECASE)


def classify_by_rules(
    *,
    title: str,
    comment: str = "",
    description: str = "",
) -> ClassifyResult:
    blob = f"{comment}\n{title}\n{description}"
    lesson_key = parse_lesson_key_from_text(comment) or parse_lesson_key_from_text(title)
    recorded = parse_date_from_name(comment) or parse_date_from_name(title)
    kind = ""
    conf = 0.4
    if _PRACTICE_RE.search(blob):
        kind = "practice"
        conf = 0.75
    elif _BROADCAST_RE.search(blob):
        kind = "broadcast"
        conf = 0.7
    elif _LESSON_RE.search(blob) or lesson_key:
        kind = "lesson_video"
        conf = 0.65 if lesson_key else 0.5
    else:
        kind = "other"
        conf = 0.35
    if lesson_key:
        conf = min(0.95, conf + 0.15)
    return ClassifyResult(
        lesson_key=lesson_key,
        kind=kind,
        recorded_on=recorded,
        confidence=conf,
        title=title,
        source="rules",
    )


async def classify_video(
    *,
    probe: VideoProbe,
    comment: str,
    lessons: Sequence[dict],
    llm: Optional[CourseLLM],
    user_id: int = 0,
) -> ClassifyResult:
    rules = classify_by_rules(
        title=probe.title or "",
        comment=comment,
        description=probe.description or "",
    )
    if rules.lesson_key and rules.kind in ("lesson_video", "practice", "broadcast") and rules.confidence >= 0.7:
        return rules
    if llm is None:
        return rules
    from config import config

    model = getattr(config, "COURSE_PLANNER_MODEL", "gpt-4o-mini")
    lesson_lines = "\n".join(
        f"- {row.get('lesson_key')}: {row.get('title') or ''}"
        for row in lessons
    ) or "(уроков пока нет)"
    system = (
        "Ты классифицируешь видео эксперта для базы курса. "
        "Верни JSON: {\"lesson_key\":\"2.4 или пусто\",\"kind\":\"lesson_video|practice|broadcast|other\","
        "\"recorded_on\":\"YYYY-MM-DD или пусто\",\"confidence\":0.0}. "
        "kind: lesson_video — запись урока; practice — Zoom-практика; broadcast — эфир; other — прочее. "
        "Не выдумывай номер урока, которого нет в списке, если только он явно не назван."
    )
    user = (
        f"Комментарий: {comment or '—'}\n"
        f"Название: {probe.title}\n"
        f"Описание (начало): {(probe.description or '')[:800]}\n"
        f"Дата хостинга: {probe.recorded_on or '—'}\n"
        f"Уроки активного продукта:\n{lesson_lines}\n"
        f"Эвристика: lesson_key={rules.lesson_key} kind={rules.kind}"
    )
    try:
        data = await llm.complete_json(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            user_id=user_id,
            temperature=0.1,
            max_tokens=400,
            request_kind="course_classify",
        )
    except Exception as e:
        logger.warning("classify llm: %s", e)
        return rules
    kind = str(data.get("kind") or rules.kind).strip()
    if kind not in ("lesson_video", "practice", "broadcast", "other"):
        kind = rules.kind
    lk = str(data.get("lesson_key") or "").strip()
    known = {str(r.get("lesson_key")) for r in lessons}
    if lk and known and lk not in known and lk != rules.lesson_key:
        if not re.match(r"^\d{1,2}(?:\.\d{1,2})?$", lk):
            lk = rules.lesson_key
    rec = None
    rec_s = str(data.get("recorded_on") or "").strip()
    if rec_s:
        try:
            rec = datetime.strptime(rec_s[:10], "%Y-%m-%d").date()
        except ValueError:
            rec = rules.recorded_on
    try:
        conf = float(data.get("confidence") or 0.6)
    except (TypeError, ValueError):
        conf = 0.6
    return ClassifyResult(
        lesson_key=lk or rules.lesson_key,
        kind=kind,
        recorded_on=rec or rules.recorded_on or probe.recorded_on,
        confidence=conf,
        title=probe.title,
        source="llm",
    )
