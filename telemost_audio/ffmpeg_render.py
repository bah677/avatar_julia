"""Нарезка аудио в OGG Opus для голосовых сообщений Telegram."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Sequence

from config import config
from telemost_audio.moments_llm import AudioClipMoment

logger = logging.getLogger(__name__)


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _timing_offset_sec() -> float:
    return float(
        getattr(config, "TELEMOST_AUDIO_CLIPS_OFFSET_SEC", -2.5) or -2.5
    )


def _render_one_sync(
    audio_path: Path,
    moment: AudioClipMoment,
    out_path: Path,
    *,
    max_duration_sec: float,
) -> bool:
    offset = _timing_offset_sec()
    start = max(0.0, moment.start_sec + offset)
    end = min(moment.end_sec + offset, start + max_duration_sec)
    duration = end - start
    if duration < 35:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(audio_path),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        "-vbr",
        "on",
        "-application",
        "voip",
        "-compression_level",
        "10",
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            logger.error(
                "ffmpeg audio clip %s: %s",
                out_path.name,
                (proc.stderr or "")[-600:],
            )
            return False
        return out_path.is_file() and out_path.stat().st_size > 5000
    except Exception as e:
        logger.exception("ffmpeg audio render: %s", e)
        return False


async def render_audio_clips(
    audio_path: str,
    moments: Sequence[AudioClipMoment],
    *,
    work_dir: str | Path,
    max_duration_sec: int = 60,
) -> List[Path]:
    src = Path(audio_path)
    if not src.is_file():
        return []
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    out_paths: List[Path] = []
    max_dur = float(max_duration_sec)
    for i, moment in enumerate(moments, start=1):
        out = root / f"voice_{i:02d}.ogg"
        ok = await asyncio.to_thread(
            _render_one_sync,
            src,
            moment,
            out,
            max_duration_sec=max_dur,
        )
        if ok:
            out_paths.append(out)
    return out_paths
