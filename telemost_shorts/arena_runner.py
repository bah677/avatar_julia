"""Опциональная пост-обработка через @whitegodkingsley/arena-cli."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def _arena_bin() -> Optional[str]:
    return shutil.which("arena")


async def format_clips_for_shorts(
    clip_paths: List[Path],
    *,
    work_dir: Path,
    platform: str = "youtube-shorts",
) -> List[Path]:
    """Если arena установлен — форматирует клипы под 9:16; иначе возвращает исходные."""
    arena = _arena_bin()
    if not arena or not clip_paths:
        return clip_paths

    in_dir = work_dir / "arena_in"
    out_dir = work_dir / "arena_out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    for p in clip_paths:
        dest = in_dir / p.name
        if p.resolve() != dest.resolve():
            shutil.copy2(p, dest)

    cmd = [
        arena,
        "format",
        str(in_dir) + "/",
        "-p",
        platform,
        "-o",
        str(out_dir) + "/",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "arena format failed (%s), using ffmpeg output: %s",
                proc.returncode,
                (stderr.decode(errors="replace") if stderr else "")[-500:],
            )
            return clip_paths
        formatted = sorted(out_dir.glob("*.mp4"))
        if formatted:
            logger.info("arena format: %s clips", len(formatted))
            return formatted
    except FileNotFoundError:
        logger.info("arena CLI not found, skip format step")
    except Exception as e:
        logger.warning("arena format: %s", e)
    return clip_paths
