"""json3 / VTT → SpeechSegment."""

from __future__ import annotations

import json
import re
from typing import List

from course.speech import SpeechSegment


def parse_json3(raw: str) -> List[SpeechSegment]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    out: List[SpeechSegment] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        segs = ev.get("segs") or []
        text = "".join(str(s.get("utf8") or "") for s in segs if isinstance(s, dict))
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        start_ms = float(ev.get("tStartMs") or 0)
        dur_ms = float(ev.get("dDurationMs") or 0)
        start = start_ms / 1000.0
        end = start + (dur_ms / 1000.0 if dur_ms else max(1.0, len(text) / 14.0))
        out.append(SpeechSegment(start_sec=start, end_sec=end, text=text))
    return out


_VTT_TS = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)


def _hms(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(raw: str) -> List[SpeechSegment]:
    """VTT с удалением «бегущих» повторов (типично для автосубтитров)."""
    lines = (raw or "").replace("\r\n", "\n").split("\n")
    out: List[SpeechSegment] = []
    i = 0
    prev_text = ""
    while i < len(lines):
        m = _VTT_TS.search(lines[i])
        if not m:
            i += 1
            continue
        start = _hms(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _hms(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        text_lines: List[str] = []
        while i < len(lines) and lines[i].strip() and not _VTT_TS.search(lines[i]):
            t = re.sub(r"<[^>]+>", "", lines[i]).strip()
            if t and t != prev_text:
                text_lines.append(t)
            i += 1
        text = " ".join(text_lines).strip()
        if text:
            if out and text.startswith(out[-1].text) and len(text) - len(out[-1].text) < 40:
                out[-1] = SpeechSegment(
                    start_sec=out[-1].start_sec,
                    end_sec=end,
                    text=text,
                )
            else:
                out.append(SpeechSegment(start_sec=start, end_sec=end, text=text))
            prev_text = text
    return out


def chars_per_minute(segments: List[SpeechSegment]) -> float:
    if not segments:
        return 0.0
    dur = max(s.end_sec for s in segments) - min(s.start_sec for s in segments)
    if dur <= 1:
        return 0.0
    chars = sum(len(s.text) for s in segments)
    return chars / (dur / 60.0)
