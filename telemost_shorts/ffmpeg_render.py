"""Нарезка вертикальных клипов с субтитрами (ffmpeg)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Sequence

from config import config
from telemost_mail.timestamped_speech import SpeechSegment, merge_segments_window
from telemost_shorts.moments_llm import ClipMoment

logger = logging.getLogger(__name__)

_PLAY_W = 1080
_MARGIN_LR = 52
_FONT_SIZE = 54
_REF_CHARS = 32


def _sec_to_ass_time(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h = cs // 360_000
    cs %= 360_000
    m = cs // 6_000
    cs %= 6_000
    s = cs // 100
    cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _wrap_subtitle_text(
    text: str,
    *,
    max_lines: int = 2,
    max_chars_per_line: int = 34,
) -> str:
    """Две строки внизу: перенос по словам, без простыни на весь экран."""
    clean = " ".join((text or "").replace("\n", " ").split())
    if not clean:
        return ""
    if len(clean) <= max_chars_per_line:
        return clean

    words = clean.split()
    lines: List[str] = []
    current: List[str] = []
    current_len = 0
    for word in words:
        extra = len(word) + (1 if current else 0)
        if current and current_len + extra > max_chars_per_line:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
            if len(lines) >= max_lines:
                break
        else:
            current.append(word)
            current_len += extra
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    elif current and len(lines) == max_lines:
        tail = " ".join(current)
        last = lines[-1]
        room = max_chars_per_line - len(last) - 1
        if room >= 4:
            lines[-1] = f"{last} {tail[:room].rstrip()}…"
        else:
            lines[-1] = f"{last.rstrip('…')}…"

    return "\\N".join(lines[:max_lines])


def _format_stretched_ass_text(wrapped: str) -> str:
    """Растягивает каждую строку по ширине кадра (fscx), поля MarginL/R в стиле."""
    parts = wrapped.split("\\N")
    styled: List[str] = []
    for line in parts:
        n = max(1, len(line))
        scale_x = int(min(158, max(108, 100 * _REF_CHARS / n)))
        styled.append(f"{{\\fscx{scale_x}}}{line}")
    return "\\N".join(styled)


def _subtitle_offset_sec() -> float:
    return float(
        getattr(config, "TELEMOST_SHORTS_SUBTITLE_OFFSET_SEC", -2.5) or -2.5
    )


def _write_ass(
    path: Path,
    segments: Sequence[SpeechSegment],
    *,
    clip_start: float,
    clip_end: float,
) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,Arial,{_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,{_MARGIN_LR},{_MARGIN_LR},150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""".format(
        _FONT_SIZE=_FONT_SIZE,
        _MARGIN_LR=_MARGIN_LR,
    )
    offset = _subtitle_offset_sec()
    clip_len = max(0.1, clip_end - clip_start)
    events: List[str] = []
    for seg in merge_segments_window(list(segments), clip_start, clip_end):
        rel_start = max(0.0, seg.start_sec - clip_start + offset)
        rel_end = min(clip_len, seg.end_sec - clip_start + offset)
        if rel_end <= rel_start:
            rel_end = min(clip_len, rel_start + 2.5)
        text = _format_stretched_ass_text(_wrap_subtitle_text(seg.text or ""))
        if not text:
            continue
        events.append(
            f"Dialogue: 0,{_sec_to_ass_time(rel_start)},{_sec_to_ass_time(rel_end)},"
            f"Shorts,,0,0,0,,{text}"
        )
    if not events:
        events.append(
            "Dialogue: 0,0:00:00.00,0:00:03.00,Shorts,,0,0,0,,…"
        )
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _render_one_sync(
    video_path: Path,
    moment: ClipMoment,
    segments: Sequence[SpeechSegment],
    out_path: Path,
    *,
    max_duration_sec: float,
) -> bool:
    start = max(0.0, moment.start_sec)
    end = min(moment.end_sec, start + max_duration_sec)
    duration = end - start
    if duration < 5:
        return False

    work = out_path.parent
    work.mkdir(parents=True, exist_ok=True)
    ass = work / f"{out_path.stem}.ass"
    _write_ass(ass, segments, clip_start=start, clip_end=end)

    ass_esc = str(ass.resolve()).replace("\\", "/").replace(":", r"\:")
    vf = (
        "crop=min(iw\\,ih*9/16):ih:(iw-min(iw\\,ih*9/16))/2:0,"
        "scale=1080:1920:flags=lanczos,"
        f"subtitles={ass_esc}"
    )
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            logger.error(
                "ffmpeg clip failed: %s\nstderr: %s",
                out_path.name,
                (proc.stderr or "")[-800:],
            )
            return False
        return out_path.is_file() and out_path.stat().st_size > 0
    except Exception as e:
        logger.exception("ffmpeg render: %s", e)
        return False


async def render_vertical_clips(
    video_path: str,
    moments: Sequence[ClipMoment],
    segments: Sequence[SpeechSegment],
    *,
    work_dir: str | Path,
    max_duration_sec: int = 60,
) -> List[Path]:
    src = Path(video_path)
    if not src.is_file():
        return []
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    out_paths: List[Path] = []
    for i, moment in enumerate(moments, start=1):
        out = root / f"short_{i:02d}.mp4"
        ok = await asyncio.to_thread(
            _render_one_sync,
            src,
            moment,
            segments,
            out,
            max_duration_sec=float(max_duration_sec),
        )
        if ok:
            out_paths.append(out)
    return out_paths
