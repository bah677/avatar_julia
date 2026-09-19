"""LLM: 5 виральных моментов эфира (≤60 с) в духе философии эксперта."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import List, Sequence

from telemost_mail.timestamped_speech import SpeechSegment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClipMoment:
    start_sec: float
    end_sec: float
    title: str
    hook: str
    reason: str

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


_SYSTEM = """Ты монтажёр коротких вертикальных роликов (Shorts/Reels) для духовного наставника.

Задача: выбрать ровно {count} отрывков из расшифровки эфира.
Каждый отрывок:
- длительность строго от 20 до {max_sec} секунд (end_sec - start_sec);
- только речь эксперта (Константин / Костя);
- эмоционально сильный, «цепляющий», мотивирует досмотреть запись целиком;
- отражает философию: разговор с Богом, честность, глубина, без кликбейта и пустых обещаний.

Верни ТОЛЬКО JSON:
{{
  "clips": [
    {{
      "start_sec": 61.0,
      "end_sec": 118.0,
      "title": "короткий заголовок",
      "hook": "фраза для подписи в Telegram",
      "reason": "почему это вирально"
    }}
  ]
}}

Не пересекай отрывки сильно; start/end должны попадать в реальные таймкоды реплик."""


def _segments_for_prompt(
    segments: Sequence[SpeechSegment],
    limit: int = 400,
    *,
    skip_first: int = 0,
) -> str:
    pool = list(segments)[skip_first : skip_first + limit]
    lines: List[str] = []
    for s in pool:
        lines.append(
            f"[{s.start_sec:.0f}s] {s.text[:220]}"
        )
    return "\n".join(lines)


def _clamp_moment(
    m: ClipMoment,
    *,
    max_sec: float,
    max_end: float,
) -> ClipMoment:
    start = max(0.0, float(m.start_sec))
    end = min(float(m.end_sec), max_end)
    if end <= start:
        end = min(start + max_sec, max_end)
    dur = end - start
    if dur > max_sec:
        end = start + max_sec
    if dur < 15 and end < max_end:
        end = min(start + min(max_sec, 45), max_end)
    return ClipMoment(
        start_sec=start,
        end_sec=end,
        title=(m.title or "")[:120],
        hook=(m.hook or "")[:300],
        reason=(m.reason or "")[:300],
    )


def _parse_clips(raw: str, *, max_sec: float, max_end: float) -> List[ClipMoment]:
    text = (raw or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: List[ClipMoment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            cm = ClipMoment(
                start_sec=float(item.get("start_sec", 0)),
                end_sec=float(item.get("end_sec", 0)),
                title=str(item.get("title") or "").strip(),
                hook=str(item.get("hook") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
            )
            out.append(_clamp_moment(cm, max_sec=max_sec, max_end=max_end))
        except (TypeError, ValueError):
            continue
    return out


def _fallback_moments(
    segments: Sequence[SpeechSegment],
    *,
    count: int,
    max_sec: float,
    regenerate: bool = False,
) -> List[ClipMoment]:
    """Если LLM недоступен — самые длинные осмысленные окна."""
    if not segments:
        return []
    scored = sorted(
        segments,
        key=lambda s: len(s.text),
        reverse=True,
    )
    if regenerate:
        head = scored[: max(count * 4, 12)]
        random.shuffle(head)
        scored = head + [s for s in scored if s not in head]
    out: List[ClipMoment] = []
    used_starts: List[float] = []
    for seg in scored:
        if len(out) >= count:
            break
        if any(abs(seg.start_sec - u) < max_sec * 0.5 for u in used_starts):
            continue
        end = min(seg.end_sec + max_sec * 0.6, seg.start_sec + max_sec)
        if end - seg.start_sec < 15:
            end = seg.start_sec + min(max_sec, 45)
        out.append(
            ClipMoment(
                start_sec=seg.start_sec,
                end_sec=end,
                title=seg.text[:80],
                hook=seg.text[:160],
                reason="fallback: длинная реплика эксперта",
            )
        )
        used_starts.append(seg.start_sec)
    return out


async def pick_viral_moments(
    segments: Sequence[SpeechSegment],
    *,
    philosophy_hint: str,
    meeting_title: str,
    count: int = 5,
    max_duration_sec: int = 60,
    regenerate: bool = False,
) -> List[ClipMoment]:
    from config import config

    if not segments:
        return []

    max_sec = max(20, min(60, int(max_duration_sec)))
    max_end = max(s.end_sec for s in segments) + 5.0
    skip_first = 0
    if regenerate and len(segments) > 20:
        skip_first = random.randint(0, min(60, len(segments) // 5))
    prompt_body = _segments_for_prompt(segments, skip_first=skip_first)
    hint = (philosophy_hint or "").strip()
    title = (meeting_title or "Эфир").strip()
    variation = int(time.time()) % 10_000

    user = f"Эфир: {title}\n\nРасшифровка (таймкод → текст):\n{prompt_body}"
    if regenerate:
        user = (
            f"Повторный запрос #{variation}. Нужны НОВЫЕ отрывки — другие таймкоды "
            f"и другие темы, не повторяй типичный «безопасный» набор.\n"
            f"Ищи неожиданные сильные моменты в других частях эфира.\n\n{user}"
        )
    if hint:
        user = f"Философия эксперта:\n{hint}\n\n{user}"

    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    temperature = 0.92 if regenerate else 0.35
    if key and prompt_body:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            sys_prompt = _SYSTEM.format(count=count, max_sec=max_sec)
            if regenerate:
                sys_prompt += (
                    "\n\nВажно: это перегенерация. Выбери другие отрывки, "
                    "чем могли бы выбрать в прошлый раз."
                )
            r = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user[:12000]},
                ],
                max_tokens=1200,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            out = r.choices[0].message.content if r.choices else ""
            clips = _parse_clips(out or "", max_sec=max_sec, max_end=max_end)
            if clips:
                return clips[:count]
        except Exception as e:
            logger.warning("pick_viral_moments LLM: %s", e)

    return _fallback_moments(
        segments, count=count, max_sec=max_sec, regenerate=regenerate
    )[:count]
