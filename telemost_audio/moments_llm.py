"""LLM: мини-подкасты ~1 мин из эфира → подпись к голосовому в Telegram."""

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
class AudioClipMoment:
    start_sec: float
    end_sec: float
    title: str
    hook: str
    reason: str

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


_SYSTEM = """Ты редактор аудио-мини-подкастов (≈1 мин) для духовного наставника.

Задача: выбрать ровно {count} отрывков из расшифровки эфира.
Каждый отрывок:
- длительность от 45 до {max_sec} секунд (end_sec - start_sec);
- только речь эксперта;
- законченная мысль, которую интересно дослушать;
- hook — ОДНО короткое яркое предложение для подписи к голосовому в Telegram (до 120 символов), чтобы захотелось нажать и послушать;
- без кликбейта, в духе честного разговора с Богом.

Верни ТОЛЬКО JSON:
{{
  "clips": [
    {{
      "start_sec": 61.0,
      "end_sec": 118.0,
      "title": "тема отрывка",
      "hook": "Одно предложение — почему стоит послушать.",
      "reason": "почему этот фрагмент силён"
    }}
  ]
}}

start/end — реальные секунды из расшифровки. Отрывки не должны сильно пересекаться."""


def _segments_for_prompt(
    segments: Sequence[SpeechSegment],
    limit: int = 400,
    *,
    skip_first: int = 0,
) -> str:
    pool = list(segments)[skip_first : skip_first + limit]
    return "\n".join(f"[{s.start_sec:.0f}s] {s.text[:220]}" for s in pool)


def _clamp_moment(
    m: AudioClipMoment,
    *,
    max_sec: float,
    max_end: float,
) -> AudioClipMoment:
    start = max(0.0, float(m.start_sec))
    end = min(float(m.end_sec), max_end)
    if end <= start:
        end = min(start + max_sec, max_end)
    dur = end - start
    if dur > max_sec:
        end = start + max_sec
    if dur < 40 and end < max_end:
        end = min(start + min(max_sec, 58), max_end)
    hook = (m.hook or m.title or "").strip()
    if hook.count(".") > 1:
        hook = hook.split(".")[0].strip() + "."
    if len(hook) > 120:
        hook = hook[:117].rstrip() + "…"
    return AudioClipMoment(
        start_sec=start,
        end_sec=end,
        title=(m.title or "")[:120],
        hook=hook[:120],
        reason=(m.reason or "")[:300],
    )


def _parse_clips(raw: str, *, max_sec: float, max_end: float) -> List[AudioClipMoment]:
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
    out: List[AudioClipMoment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            cm = AudioClipMoment(
                start_sec=float(item.get("start_sec", 0)),
                end_sec=float(item.get("end_sec", 0)),
                title=str(item.get("title") or "").strip(),
                hook=str(item.get("hook") or item.get("title") or "").strip(),
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
) -> List[AudioClipMoment]:
    if not segments:
        return []
    scored = sorted(segments, key=lambda s: len(s.text), reverse=True)
    if regenerate:
        head = scored[: max(count * 4, 12)]
        random.shuffle(head)
        scored = head + [s for s in scored if s not in head]
    out: List[AudioClipMoment] = []
    used: List[float] = []
    for seg in scored:
        if len(out) >= count:
            break
        if any(abs(seg.start_sec - u) < max_sec * 0.45 for u in used):
            continue
        end = min(seg.end_sec + max_sec * 0.5, seg.start_sec + max_sec)
        if end - seg.start_sec < 40:
            end = seg.start_sec + min(max_sec, 55)
        hook = seg.text.split(".")[0].strip()[:120]
        if hook and not hook.endswith((".", "!", "?", "…")):
            hook += "."
        out.append(
            AudioClipMoment(
                start_sec=seg.start_sec,
                end_sec=end,
                title=seg.text[:80],
                hook=hook or seg.text[:120],
                reason="fallback",
            )
        )
        used.append(seg.start_sec)
    return out


async def pick_audio_moments(
    segments: Sequence[SpeechSegment],
    *,
    philosophy_hint: str,
    meeting_title: str,
    count: int = 5,
    max_duration_sec: int = 60,
    regenerate: bool = False,
) -> List[AudioClipMoment]:
    from config import config

    if not segments:
        return []

    max_sec = max(45, min(60, int(max_duration_sec)))
    max_end = max(s.end_sec for s in segments) + 5.0
    skip_first = 0
    if regenerate and len(segments) > 20:
        skip_first = random.randint(0, min(60, len(segments) // 5))
    prompt_body = _segments_for_prompt(segments, skip_first=skip_first)
    hint = (philosophy_hint or "").strip()
    title = (meeting_title or "Эфир").strip()
    variation = int(time.time()) % 10_000

    user = f"Эфир: {title}\n\nРасшифровка (сек → текст):\n{prompt_body}"
    if regenerate:
        user = (
            f"Повтор #{variation}: нужны ДРУГИЕ отрывки (~1 мин), другие таймкоды.\n\n{user}"
        )
    if hint:
        user = f"Философия:\n{hint}\n\n{user}"

    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    temperature = 0.9 if regenerate else 0.4
    if key and prompt_body:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            sys_prompt = _SYSTEM.format(count=count, max_sec=max_sec)
            if regenerate:
                sys_prompt += "\n\nВыбери другие фрагменты, чем в прошлый раз."
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
            logger.warning("pick_audio_moments LLM: %s", e)

    return _fallback_moments(
        segments, count=count, max_sec=max_sec, regenerate=regenerate
    )[:count]
