"""Реплики эксперта с таймкодами из TXT Телемоста."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence

from telemost_mail.transcript import _name_matches

_TS_RE = re.compile(
    r"^\[(\d{1,2}):(\d{2}):(\d{2})\]\s*(.*)$"
)
_SPEAKER_HEADER_RE = re.compile(r"^([^:\[\n]{2,120}?)\s*:\s*$")


@dataclass(frozen=True)
class SpeechSegment:
    start_sec: float
    text: str
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def _hms_to_sec(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_expert_segments(
    transcript: str,
    speaker_names: Sequence[str],
) -> List[SpeechSegment]:
    lines = (transcript or "").splitlines()
    aliases = list(speaker_names)
    raw: List[tuple[float, str]] = []
    current_speaker: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if _SPEAKER_HEADER_RE.match(line):
            current_speaker = _SPEAKER_HEADER_RE.match(line).group(1).strip()
            continue
        m = _TS_RE.match(line)
        if m and current_speaker and _name_matches(current_speaker, aliases):
            sec = _hms_to_sec(m.group(1), m.group(2), m.group(3))
            text = (m.group(4) or "").strip()
            if text:
                raw.append((sec, text))

    if not raw:
        return []

    out: List[SpeechSegment] = []
    for i, (start, text) in enumerate(raw):
        if i + 1 < len(raw):
            end = raw[i + 1][0]
        else:
            end = start + max(4.0, min(12.0, len(text) / 14.0))
        out.append(SpeechSegment(start_sec=start, text=text, end_sec=end))
    return out


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
