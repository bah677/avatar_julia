#!/usr/bin/env python3
"""Ручной прогон разбора: ссылка на видео или локальный документ → карточки в консоль.

  python3 scripts/course_mining_probe.py 'https://youtu.be/…'
  python3 scripts/course_mining_probe.py --file path/to.pdf --kind summary
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _NullStorage:
    async def log_llm_completion_usage(self, **kwargs):
        return None

    async def log_interaction(self, **kwargs):
        return None


async def _from_url(url: str) -> tuple[str, str, list]:
    from course.paths import tmp_dir
    from course.transcribe import transcribe_source_video
    from course.video_hosts import adapter_for, detect_host
    from openai_client.assistant import OpenAIClient

    host = detect_host(url) or "youtube"
    adapter = adapter_for(host)
    probe = await adapter.probe(url)
    title = (probe.title if probe else "") or url
    duration = probe.duration_sec if probe else None
    client = OpenAIClient(_NullStorage())
    dest = tmp_dir() / "mining_probe"
    dest.mkdir(parents=True, exist_ok=True)
    segs, method = await transcribe_source_video(
        adapter=adapter,
        url=url,
        openai_client=client,
        user_id=0,
        dest_dir=str(dest),
        duration_sec=duration,
    )
    from course.mining import mining_chunks_from_segments

    chunks = mining_chunks_from_segments(segs)
    kind = "lesson_video" if host in ("youtube", "vimeo", "kinescope") else "other"
    print(f"хост={host} метод={method or 'нет'} сегментов={len(segs)} кусков={len(chunks)}")
    if duration:
        print(f"длительность ≈ {duration} сек")
    return kind, title, chunks


async def _from_file(path: Path, kind: str) -> tuple[str, str, list]:
    from course.documents import extract_document
    from course.mining import mining_chunks_from_pages
    from openai_client.assistant import OpenAIClient

    client = OpenAIClient(_NullStorage())
    with tempfile.TemporaryDirectory(prefix="probe_doc_") as tmp:
        extracted = await extract_document(
            str(path),
            kind=kind,
            dest_dir=tmp,
            openai_client=client,
            user_id=0,
        )
    pages = extracted.get("pages") or []
    chunks = mining_chunks_from_pages(pages)
    print(
        f"файл={path.name} метод={extracted.get('method')} "
        f"страниц={len(pages)} кусков={len(chunks)}"
    )
    return kind, path.stem, chunks


async def _run(args: argparse.Namespace) -> int:
    from course.llm import CourseLLM
    from course.mining import mine_source, build_lesson_passport

    if args.file:
        kind, title, chunks = await _from_file(Path(args.file), args.kind)
    elif args.url:
        kind, title, chunks = await _from_url(args.url)
    else:
        print("укажите URL или --file", file=sys.stderr)
        return 2
    if not chunks:
        print("нет текста для разбора", file=sys.stderr)
        return 1

    llm = CourseLLM(None)
    cards = await mine_source(
        kind=kind,
        title=title,
        chunks=chunks,
        llm=llm,
        user_id=0,
        is_document=bool(args.file),
    )
    print(f"\nкарточек: {len(cards)}\n")
    for i, c in enumerate(cards, start=1):
        print(f"{i}. [{c.get('type')}] {c.get('title')}  score={c.get('score')}")
        print(f"   {c.get('text')}")
        if c.get("quote"):
            print(f"   «{c.get('quote')}»")
        extra = []
        if c.get("anchor_sec"):
            extra.append(f"t={c.get('anchor_sec')}s")
        if c.get("page"):
            extra.append(f"стр.{c.get('page')}")
        if extra:
            print("   " + ", ".join(extra))
        print()

    if args.passport:
        data, text = await build_lesson_passport(
            lesson_title=title,
            lesson_key=args.lesson or "?",
            summary_text="\n\n".join(chunks[:3]) if args.file else "",
            slides_text="",
            cards=cards,
            video_links=[args.url] if args.url else [],
            llm=llm,
            user_id=0,
        )
        print(text)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.json:
        print(json.dumps(cards, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url", nargs="?", help="YouTube / Vimeo / Kinescope")
    p.add_argument("--file", help="локальный pdf/docx/txt")
    p.add_argument("--kind", default="summary", help="summary|slides|practice|…")
    p.add_argument("--passport", action="store_true", help="собрать паспорт урока")
    p.add_argument("--lesson", default="", help="ключ урока для паспорта (2.4)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
