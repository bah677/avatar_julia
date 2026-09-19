"""Извлечение реплик эксперта из TXT-расшифровки Телемоста (по имени спикера)."""

from __future__ import annotations

import logging
import re
from typing import List, Sequence

logger = logging.getLogger(__name__)

# Телемост: «Константин Осколкин:» на отдельной строке, далее [00:00:06] реплика
_SPEAKER_HEADER_RE = re.compile(r"^([^:\[\n]{2,120}?)\s*:\s*$")
_TIMESTAMP_LINE_RE = re.compile(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*(.*)$")

# Legacy: «Имя: текст» в одной строке
_INLINE_RE = re.compile(
    r"^(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?"
    r"(?P<name>[^:\n]{2,80}?)\s*[:：]\s*(?P<text>.+)$",
    re.MULTILINE,
)

_NOISE_UTTERANCES = frozenset({"um", "uh", "эм", "мм", "а", "э"})


def _normalize_names(names: Sequence[str]) -> List[str]:
    return [n.strip().lower() for n in names if (n or "").strip()]


def _name_matches(speaker: str, aliases: Sequence[str]) -> bool:
    s = (speaker or "").strip().lower()
    if not s:
        return False
    for alias in aliases:
        a = alias.strip().lower()
        if not a:
            continue
        if a in s or s in a:
            return True
        # «Константин» ↔ «Константин Осколкин»
        if s.split()[0] == a.split()[0]:
            return True
    return False


def _is_noise_line(text: str) -> bool:
    t = (text or "").strip().lower()
    return len(t) <= 3 and t in _NOISE_UTTERANCES


def _extract_telemost_blocks(transcript: str, aliases: Sequence[str]) -> str:
    lines = transcript.splitlines()
    chunks: List[str] = []
    current_speaker: str | None = None
    current_lines: List[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_lines
        if current_speaker and _name_matches(current_speaker, aliases) and current_lines:
            chunks.append("\n".join(current_lines))
        current_speaker = None
        current_lines = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if _SPEAKER_HEADER_RE.match(line):
            flush()
            current_speaker = _SPEAKER_HEADER_RE.match(line).group(1).strip()
            continue

        tm = _TIMESTAMP_LINE_RE.match(line)
        if tm and current_speaker:
            text = (tm.group(1) or "").strip()
            if text and not _is_noise_line(text):
                current_lines.append(text)
            continue

    flush()
    return "\n\n".join(chunks)


def extract_expert_speech(transcript: str, speaker_names: Sequence[str]) -> str:
    """
    Реплики эксперта из расшифровки Телемоста.

    Формат Яндекса: блок ``Имя Фамилия:`` + строки ``[HH:MM:SS] текст``.
    """
    text = (transcript or "").strip()
    aliases = _normalize_names(speaker_names)
    if not text or not aliases:
        return ""

    telemost = _extract_telemost_blocks(text, aliases)
    if len(telemost.strip()) >= 40:
        return telemost

    chunks: List[str] = []
    for m in _INLINE_RE.finditer(text):
        if _name_matches(m.group("name"), aliases):
            t = (m.group("text") or "").strip()
            if t and not _is_noise_line(t):
                chunks.append(t)

    if chunks:
        return "\n\n".join(chunks)

    return telemost


async def extract_expert_speech_llm_fallback(
    transcript: str,
    speaker_names: Sequence[str],
) -> str:
    """Если эвристика ничего не нашла — LLM-проход."""
    basic = extract_expert_speech(transcript, speaker_names)
    if len(basic.strip()) >= 80:
        return basic

    from config import config

    key = (config.OPENAI_API_KEY or "").strip()
    if not key or not (transcript or "").strip():
        return basic

    names = ", ".join(speaker_names)
    sample = (transcript or "")[:12000]
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key)
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Формат: блоки «Имя:» и строки [00:00:00] реплика. "
                        "Извлеки ТОЛЬКО реплики указанных спикеров (без меток времени). "
                        "Без реплик других участников. Без пояснений."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Спикеры: {names}\n\nРасшифровка:\n{sample}",
                },
            ],
            max_tokens=4000,
            temperature=0.1,
        )
        out = (r.choices[0].message.content if r.choices else "") or ""
        return out.strip() or basic
    except Exception as e:
        logger.warning("extract_expert_speech_llm_fallback: %s", e)
        return basic
