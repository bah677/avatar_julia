"""Реплики с таймкодами (SpeechSegment) — перенос из K/telemost_mail/timestamped_speech.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence


@dataclass(frozen=True)
class SpeechSegment:
    start_sec: float
    text: str
    end_sec: float
    speaker: str = ""

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def merge_segments_window(
    segments: List[SpeechSegment],
    start_sec: float,
    end_sec: float,
) -> List[SpeechSegment]:
    return [
        s
        for s in segments
        if s.end_sec > start_sec and s.start_sec < end_sec
    ]


def format_expert_blocks_for_prompt(
    segments: Sequence[SpeechSegment],
    *,
    gap_sec: float = 12.0,
    limit_blocks: int = 90,
    skip_first_blocks: int = 0,
    max_text_per_block: int = 520,
) -> str:
    """Склеивает соседние реплики в блоки — удобнее искать целую мысль."""
    if not segments:
        return ""

    def _piece(s: SpeechSegment, last_speaker: str | None) -> tuple[str, str | None]:
        sp = (s.speaker or "").strip()
        if not sp:
            return s.text, last_speaker
        if last_speaker != sp:
            return f"{sp}: {s.text}", sp
        return s.text, sp

    blocks: List[tuple[float, float, str]] = []
    cur_start: float | None = None
    cur_end: float = 0.0
    texts: List[str] = []
    last_speaker: str | None = None
    for s in segments:
        piece, last_speaker = _piece(s, last_speaker)
        if cur_start is None:
            cur_start = s.start_sec
            cur_end = s.end_sec
            texts = [piece]
            continue
        if s.start_sec - cur_end <= gap_sec:
            texts.append(piece)
            cur_end = max(cur_end, s.end_sec)
        else:
            blocks.append((cur_start, cur_end, " ".join(texts)))
            cur_start = s.start_sec
            cur_end = s.end_sec
            last_speaker = None
            piece, last_speaker = _piece(s, last_speaker)
            texts = [piece]
    if cur_start is not None and texts:
        blocks.append((cur_start, cur_end, " ".join(texts)))

    pool = blocks[skip_first_blocks : skip_first_blocks + limit_blocks]
    lines: List[str] = []
    for a, b, text in pool:
        chunk = text.strip()
        if len(chunk) > max_text_per_block:
            chunk = chunk[: max_text_per_block - 1].rstrip() + "…"
        lines.append(f"[{a:.0f}–{b:.0f}s] {chunk}")
    return "\n\n".join(lines)


def segments_to_dicts(segments: Sequence[SpeechSegment]) -> list[dict]:
    return [
        {
            "start": float(s.start_sec),
            "end": float(s.end_sec),
            "text": s.text,
            "speaker": s.speaker or "",
        }
        for s in segments
    ]


def segments_from_dicts(rows: Sequence[dict]) -> List[SpeechSegment]:
    out: List[SpeechSegment] = []
    for row in rows or []:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(row.get("start") if "start" in row else row.get("start_sec") or 0)
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(row.get("end") if "end" in row else row.get("end_sec") or start)
        except (TypeError, ValueError):
            end = start
        out.append(
            SpeechSegment(
                start_sec=start,
                end_sec=max(end, start),
                text=text,
                speaker=str(row.get("speaker") or ""),
            )
        )
    return out
