"""Очередь источников: статусы, повторы, восстановление."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from course.documents import extract_document, split_posts
from course.llm import CourseLLM
from course.mining import (
    build_lesson_passport,
    find_duplicate_card,
    mine_source,
    mining_chunks_from_pages,
    mining_chunks_from_segments,
)
from course.paths import source_dir, tmp_dir
from course.products import EXPERT_PRODUCT_ID, active_product_id, scoped_product_ids
from course.speech import segments_from_dicts, segments_to_dicts
from course.transcribe import transcribe_source_video
from course.video_hosts import adapter_for
from rag.material_index import format_chunk_heading, v2_base_metadata
from rag.scope import active_scope, scope_from_stack

logger = logging.getLogger(__name__)

_RETRY_MINUTES = (5, 30, 120)


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class CourseWorker:
    def __init__(self, app):
        self._app = app
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._fetch_lock = asyncio.Lock()
        self._passport_tasks: Dict[int, asyncio.Task] = {}

    @property
    def storage(self):
        return self._app.user_storage

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="course_worker")
        logger.info("course worker started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(2)
        while not self._stop.is_set():
            try:
                await self.storage.recover_stale_course_sources(older_than_min=30)
                src = await self.storage.claim_next_course_source(scoped_product_ids())
                if not src:
                    await asyncio.sleep(5)
                    continue
                await self.process_source(src)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("course worker: %s", e)
                await asyncio.sleep(5)

    async def process_source(self, src: Dict[str, Any]) -> None:
        sid: UUID = src["id"]
        status = src.get("status")
        try:
            if status == "new":
                await self._stage_fetch(src)
                src = await self.storage.get_course_source(sid) or src
                status = src.get("status")
            if status == "extracted":
                await self._stage_index(src)
                src = await self.storage.get_course_source(sid) or src
                status = src.get("status")
            if status == "indexed":
                await self._stage_mine(src)
        except Exception as e:
            logger.exception("process_source %s: %s", sid, e)
            await self._fail(src, str(e))

    async def _fail(self, src: Dict[str, Any], err: str) -> None:
        attempts = int(src.get("attempts") or 0) + 1
        fields: Dict[str, Any] = {
            "attempts": attempts,
            "error_message": (err or "")[:1000],
        }
        if attempts <= 3:
            delay = _RETRY_MINUTES[min(attempts - 1, 2)]
            fields["status"] = "new" if src.get("status") in ("new", "fetching") else (
                "indexed" if src.get("status") in ("indexed", "mining") else "extracted"
            )
            fields["next_attempt_at"] = datetime.now(timezone.utc) + timedelta(minutes=delay)
        else:
            fields["status"] = "error"
            await self._admin_log(f"Источник {src.get('title')}: ошибка после 3 попыток\n<code>{err[:400]}</code>")
        await self.storage.update_course_source(src["id"], **fields)
        await self._update_intake(src, f"❌ ошибка: {err[:200]}")

    async def _stage_fetch(self, src: Dict[str, Any]) -> None:
        await self.storage.update_course_source(src["id"], status="fetching")
        await self._update_intake(src, "📝 расшифровка / извлечение текста")
        dest = source_dir(src["id"])
        origin = src.get("origin")
        kind = src.get("kind")
        user_id = int(src.get("added_by") or 0)
        openai = self._app.openai_client
        meta = _as_dict(src.get("metadata"))
        chars = 0
        method = ""

        if origin in ("youtube", "vimeo", "kinescope") or (
            origin == "disk" and kind in ("lesson_video", "practice", "broadcast", "other")
            and str(src.get("disk_path") or "").lower().endswith(
                (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac")
            )
        ):
            adapter = adapter_for(origin)
            url = src.get("url") or src.get("disk_path") or ""
            async with self._fetch_lock:
                segs, method = await transcribe_source_video(
                    adapter=adapter,
                    url=url,
                    openai_client=openai,
                    user_id=user_id,
                    dest_dir=str(tmp_dir() / str(src["id"])),
                    duration_sec=src.get("duration_sec"),
                )
            _write_json(dest / "transcript.json", {
                "method": method,
                "segments": segments_to_dicts(segs),
            })
            chars = sum(len(s.text) for s in segs)
            meta["text_method"] = method
        else:
            disk_path = src.get("disk_path") or ""
            local = dest / ("src" + Path(disk_path).suffix.lower())
            if origin == "disk" and disk_path:
                from yandex_disk.webdav import YandexDiskWebDAV
                from config import config

                dav = YandexDiskWebDAV(config.YANDEX_DISK_LOGIN, config.YANDEX_DISK_PASSWORD)
                await dav.download(disk_path, str(local))
            extracted = await extract_document(
                str(local) if local.exists() else disk_path,
                kind=kind,
                dest_dir=str(dest),
                openai_client=openai,
                user_id=user_id,
            )
            _write_json(dest / "pages.json", extracted.get("pages") or [])
            if extracted.get("vision_limit_hit"):
                await self._notify_expert(
                    "PDF без текста, лимит распознавания слайдов исчерпан. "
                    f"Файл: {src.get('title') or disk_path}"
                )
            chars = len(extracted.get("full_text") or "")
            method = extracted.get("method") or "txt"
            if kind == "post":
                posts = split_posts(extracted.get("full_text") or "")
                _write_json(dest / "posts.json", posts)
                chars = sum(len(p) for p in posts)

        _write_json(dest / "meta.json", {
            "title": src.get("title"),
            "origin": origin,
            "kind": kind,
            "url": src.get("url"),
            "disk_path": src.get("disk_path"),
            "duration_sec": src.get("duration_sec"),
        })
        await self.storage.update_course_source(
            src["id"],
            status="extracted",
            text_method=method,
            chars_count=chars,
            metadata=meta,
        )

    async def _stage_index(self, src: Dict[str, Any]) -> None:
        rs = getattr(self._app, "rag_stack", None)
        if rs is None:
            await self.storage.update_course_source(src["id"], status="indexed", chunks_count=0)
            return
        from course.products import product_display_name

        dest = source_dir(src["id"])
        lesson = None
        if src.get("lesson_id"):
            lesson = await self.storage.get_course_lesson_by_id(src["lesson_id"])
        lesson_key = (lesson or {}).get("lesson_key") or ""
        lesson_title = (lesson or {}).get("title") or ""
        product_name = product_display_name(src["product_id"])
        meta = v2_base_metadata(
            product_id=src["product_id"],
            source_id=str(src["id"]),
            source_kind=src.get("kind") or "other",
            origin=src.get("origin") or "disk",
            lesson_key=lesson_key,
            module_no=(lesson or {}).get("module_no"),
            recorded_on=str(src.get("recorded_on") or ""),
        )
        salt = f"course:{src['id']}:{src.get('disk_etag') or src.get('video_id') or ''}"
        rs.materials.delete_by_source(str(src["id"]))
        n = 0
        tr_path = dest / "transcript.json"
        pages_path = dest / "pages.json"
        if tr_path.is_file():
            data = json.loads(tr_path.read_text(encoding="utf-8"))
            segs = data.get("segments") or []

            def _h(start, end):
                return format_chunk_heading(
                    product_name=product_name,
                    lesson_key=lesson_key,
                    lesson_title=lesson_title,
                    kind=src.get("kind") or "",
                    start_sec=start,
                    end_sec=end,
                )

            n, _ = rs.materials.add_segments_text(
                segs,
                base_metadata=meta,
                source=str(src["id"])[:80],
                dedupe_salt=salt,
                heading_fn=_h,
            )
        elif pages_path.is_file():
            pages = json.loads(pages_path.read_text(encoding="utf-8"))
            page_tuples = [(int(p.get("page") or i + 1), p.get("text") or "") for i, p in enumerate(pages)]
            heading = format_chunk_heading(
                product_name=product_name,
                lesson_key=lesson_key,
                lesson_title=lesson_title,
                kind=src.get("kind") or "",
            )
            n, _ = rs.materials.add_material_text(
                "",
                base_metadata=meta,
                source=str(src["id"])[:80],
                dedupe_salt=salt,
                heading=heading,
                pages=page_tuples,
            )
        await self.storage.update_course_source(src["id"], status="indexed", chunks_count=n)

    async def _stage_mine(self, src: Dict[str, Any]) -> None:
        await self.storage.update_course_source(src["id"], status="mining")
        await self._update_intake(src, "🧠 разбор")
        dest = source_dir(src["id"])
        llm = CourseLLM(self.storage)
        user_id = int(src.get("added_by") or 0)
        kind = src.get("kind") or "other"
        cards: list[dict] = []
        tr_path = dest / "transcript.json"
        pages_path = dest / "pages.json"
        posts_path = dest / "posts.json"
        raw_log = dest / "mining.raw.jsonl"
        chunks: list[str] = []
        is_doc = False
        if tr_path.is_file():
            data = json.loads(tr_path.read_text(encoding="utf-8"))
            segs = segments_from_dicts(data.get("segments") or [])
            chunks = mining_chunks_from_segments(segs)
        elif posts_path.is_file():
            posts = json.loads(posts_path.read_text(encoding="utf-8"))
            chunks = [p for p in posts if p]
            is_doc = True
        elif pages_path.is_file():
            pages = json.loads(pages_path.read_text(encoding="utf-8"))
            chunks = mining_chunks_from_pages(pages)
            is_doc = True
        if chunks:
            cards = await mine_source(
                kind=kind,
                title=src.get("title") or "",
                chunks=chunks,
                llm=llm,
                user_id=user_id,
                duration_sec=src.get("duration_sec"),
                is_document=is_doc,
            )
            with raw_log.open("w", encoding="utf-8") as f:
                for c in cards:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")

        from config import config
        from course.video_hosts import adapter_for

        adapter = adapter_for(src.get("origin") or "youtube")
        rs = getattr(self._app, "rag_stack", None)
        gateway = None
        if rs is not None:
            try:
                gateway = scope_from_stack(rs)
            except Exception:
                gateway = None
        n_new = 0
        for card in cards:
            if gateway is not None:
                dup = await find_duplicate_card(
                    gateway, card, threshold=float(getattr(config, "COURSE_CARD_DUP_SIM", 0.88))
                )
                if dup:
                    cid = (dup.get("metadata") or {}).get("card_id")
                    if cid:
                        try:
                            await self.storage.bump_card_frequency(UUID(str(cid)), src["id"])
                            continue
                        except Exception:
                            pass
            timecode = ""
            if card.get("anchor_sec") is not None and src.get("url"):
                timecode = adapter.timecode_url(
                    src.get("url") or "",
                    float(card["anchor_sec"]),
                    video_id=src.get("video_id") or "",
                )
            meta_card = dict(card)
            meta_card["timecode_url"] = timecode
            cid = await self.storage.insert_content_card(
                product_id=src["product_id"],
                source_id=src["id"],
                lesson_id=src.get("lesson_id"),
                **{k: meta_card.get(k) for k in (
                    "type", "title", "text", "quote", "anchor_sec", "page",
                    "speaker", "audience_pain", "funnel_stage", "formats", "score",
                )},
            )
            if cid and rs is not None:
                lesson_key = ""
                if src.get("lesson_id"):
                    les = await self.storage.get_course_lesson_by_id(src["lesson_id"])
                    lesson_key = (les or {}).get("lesson_key") or ""
                rs.materials.add_card_document(
                    card_id=str(cid),
                    title=card.get("title") or "",
                    text=card.get("text") or "",
                    metadata={
                        "schema_v": 2,
                        "product_id": src["product_id"],
                        "legacy": False,
                        "source_id": str(src["id"]),
                        "source_kind": kind,
                        "lesson_key": lesson_key,
                        "card_id": str(cid),
                        "type": card.get("type") or "idea",
                        "funnel_stage": card.get("funnel_stage") or "warmup",
                        "status": "active",
                    },
                )
            if cid:
                n_new += 1

        if kind in ("post", "expert_info", "product_info"):
            await self._ingest_style_inputs(src, dest)

        await self.storage.update_course_source(
            src["id"],
            status="done",
            cards_count=n_new,
            processed_at=datetime.now(timezone.utc),
            error_message="",
        )
        await self._update_intake(src, f"✅ готово: {n_new} карточек")
        if src.get("lesson_id"):
            self._schedule_passport(int(src["lesson_id"]))
        await self._maybe_digest(src, n_new)

    async def _ingest_style_inputs(self, src: Dict[str, Any], dest: Path) -> None:
        from course.documents import split_posts
        from course.style import platform_to_format
        from rag.scope import active_scope

        rs = getattr(self._app, "rag_stack", None)
        pages_path = dest / "pages.json"
        posts_path = dest / "posts.json"
        texts: list[str] = []
        if posts_path.is_file():
            texts = json.loads(posts_path.read_text(encoding="utf-8"))
        elif pages_path.is_file():
            pages = json.loads(pages_path.read_text(encoding="utf-8"))
            full = "\n\n".join(p.get("text") or "" for p in pages)
            texts = split_posts(full) or ([full] if full.strip() else [])
        if src.get("kind") == "post" and rs is not None:
            fmt = platform_to_format(src.get("platform") or "")
            for t in texts:
                await rs.golden.add_example_async(
                    t[:200],
                    t,
                    extra_metadata={
                        "schema_v": 2,
                        "product_id": src["product_id"],
                        "legacy": False,
                        "format": fmt,
                        "seed": True,
                        "lesson_key": "",
                    },
                )
        from course.style import source_hash
        # trigger style rebuild unless manual profile exists
        product_id = src["product_id"] if src["product_id"] != EXPERT_PRODUCT_ID else active_product_id()
        profile = await self.storage.get_active_style_profile(product_id)
        if profile and profile.get("origin") == "manual":
            return
        asyncio.create_task(self._rebuild_style(product_id), name=f"style_{product_id}")

    async def _rebuild_style(self, product_id: str) -> None:
        try:
            from course.style import build_style_passport, source_hash
            from course.llm import CourseLLM

            llm = CourseLLM(self.storage)
            expert = await self._load_info_text(EXPERT_PRODUCT_ID, "expert_info")
            product = await self._load_info_text(product_id, "product_info")
            posts = await self._load_posts_text(product_id)
            text = await build_style_passport(
                posts_text=posts,
                expert_info=expert,
                product_info=product,
                speech_samples="",
                feedback_rules="",
                llm=llm,
                user_id=0,
            )
            if text:
                await self.storage.insert_style_profile(
                    product_id=product_id,
                    text=text,
                    origin="auto",
                    source_hash=source_hash([expert, product, posts]),
                )
        except Exception as e:
            logger.warning("rebuild style: %s", e)

    async def _load_info_text(self, product_id: str, kind: str) -> str:
        rows = await self.storage.list_course_sources_by_product(
            [product_id], statuses=["done"]
        )
        for r in rows:
            if r.get("kind") == kind:
                pages = source_dir(r["id"]) / "pages.json"
                if pages.is_file():
                    data = json.loads(pages.read_text(encoding="utf-8"))
                    return "\n\n".join(p.get("text") or "" for p in data)
        return ""

    async def _load_posts_text(self, product_id: str) -> str:
        rows = await self.storage.list_course_sources_by_product(
            [product_id, EXPERT_PRODUCT_ID], statuses=["done"]
        )
        parts = []
        for r in rows:
            if r.get("kind") != "post":
                continue
            p = source_dir(r["id"]) / "posts.json"
            if p.is_file():
                parts.extend(json.loads(p.read_text(encoding="utf-8")))
        return "\n\n---\n\n".join(parts[:40])

    def _schedule_passport(self, lesson_id: int) -> None:
        from config import config

        delay = int(getattr(config, "COURSE_PASSPORT_DEBOUNCE_SEC", 600) or 600)
        old = self._passport_tasks.get(lesson_id)
        if old and not old.done():
            old.cancel()

        async def _run():
            await asyncio.sleep(delay)
            await self.rebuild_lesson_passport(lesson_id)

        self._passport_tasks[lesson_id] = asyncio.create_task(
            _run(), name=f"passport_{lesson_id}"
        )

    async def rebuild_lesson_passport(self, lesson_id: int) -> None:
        lesson = await self.storage.get_course_lesson_by_id(lesson_id)
        if not lesson:
            return
        sources = await self.storage.list_course_sources_for_lesson(lesson_id)
        summary = slides = ""
        links = []
        for s in sources:
            dest = source_dir(s["id"])
            pages = dest / "pages.json"
            text = ""
            if pages.is_file():
                data = json.loads(pages.read_text(encoding="utf-8"))
                text = "\n\n".join(p.get("text") or "" for p in data)
            if s.get("kind") == "summary":
                summary = text
            elif s.get("kind") == "slides":
                slides = text
            if s.get("url"):
                links.append(s["url"])
        cards = await self.storage.list_content_cards(
            product_id=lesson["product_id"], lesson_id=lesson_id, limit=40
        )
        llm = CourseLLM(self.storage)
        data, text = await build_lesson_passport(
            lesson_title=lesson.get("title") or "",
            lesson_key=lesson.get("lesson_key") or "",
            summary_text=summary,
            slides_text=slides,
            cards=cards,
            video_links=links,
            llm=llm,
            user_id=0,
        )
        await self.storage.update_lesson_passport(lesson_id, passport=data, passport_text=text)
        n_cards = sum(1 for s in sources if s.get("status") == "done")
        total_cards = await self.storage.list_content_cards(
            product_id=lesson["product_id"], lesson_id=lesson_id, limit=200
        )
        kinds = {s.get("kind") for s in sources if s.get("status") == "done"}
        if "lesson_video" in kinds or "summary" in kinds:
            await self._notify_expert(
                f"Урок {lesson['lesson_key']} разобран: паспорт + {len(total_cards)} карточек",
                lesson_id=lesson_id,
            )

    async def remine(self, source_id: UUID) -> None:
        src = await self.storage.get_course_source(source_id)
        if not src:
            return
        await self.storage.archive_cards_for_source(source_id)
        await self.storage.update_course_source(source_id, status="indexed", attempts=0, error_message="")
        await self._stage_mine(await self.storage.get_course_source(source_id))

    async def _maybe_digest(self, src: Dict[str, Any], n_new: int) -> None:
        if src.get("kind") not in ("practice", "broadcast") or n_new <= 0:
            return
        studio = self._app.feature_manager.get_optional("content_studio")
        if studio and hasattr(studio, "send_practice_digest"):
            await studio.send_practice_digest(src)

    async def _update_intake(self, src: Dict[str, Any], text: str) -> None:
        chat_id = src.get("intake_chat_id")
        msg_id = src.get("intake_message_id")
        if not chat_id or not msg_id:
            return
        try:
            await self._app.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(msg_id),
                text=f"🎬 {src.get('title') or 'видео'}\n{text}",
            )
        except Exception:
            pass

    async def _admin_log(self, html: str) -> None:
        from bot.utils.rag_admin_context import rag_admin_chat_topic
        from aiogram.enums import ParseMode

        chat, topic = rag_admin_chat_topic()
        if not chat:
            logger.info("admin log: %s", html)
            return
        kwargs = {"parse_mode": ParseMode.HTML}
        if topic:
            kwargs["message_thread_id"] = topic
        try:
            await self._app.bot.send_message(chat, html, **kwargs)
        except Exception as e:
            logger.warning("admin log send: %s", e)

    async def _notify_expert(self, text: str, lesson_id: Optional[int] = None) -> None:
        studio = self._app.feature_manager.get_optional("content_studio")
        if studio and hasattr(studio, "notify_admins"):
            await studio.notify_admins(text, lesson_id=lesson_id)
        else:
            await self._admin_log(text)
