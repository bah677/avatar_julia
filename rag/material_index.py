"""
Индексация сырых материалов: чанкинг v2, метаданные, запись в expert_materials.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Dict, List, Optional, Sequence

from rag.chunking import pack_sentences, split_sentences
from rag.settings import RAGSettings
from rag.types import normalize_chroma_metadata
from rag.vector_store import VectorStoreService

logger = logging.getLogger(__name__)

KIND_LABELS = {
    "lesson_video": "видео урока",
    "practice": "практика",
    "broadcast": "эфир",
    "summary": "конспект",
    "slides": "слайды",
    "extra": "доп. материал",
    "other": "другое",
    "post": "пост",
    "expert_info": "об эксперте",
    "product_info": "о продукте",
    "stories": "сторис",
    "expert_reply": "ответ эксперта",
    "testimonial": "отзыв",
}


def dedupe_material_key(
    source: str, text: str, chunk_index: int, *, dedupe_salt: str = ""
) -> str:
    """Стабильный id чанка для дедупликации (хеш + источник + индекс + соль)."""
    head = (text or "")[:200]
    salt = (dedupe_salt or "").strip()
    h = hashlib.md5(f"{head}|{source}|{salt}".encode("utf-8")).hexdigest()[:16]
    return f"{h}:{source[:80]}:{chunk_index}"


def format_chunk_heading(
    *,
    product_name: str,
    lesson_key: str = "",
    lesson_title: str = "",
    kind: str = "",
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    page: Optional[int] = None,
) -> str:
    kind_l = KIND_LABELS.get(kind, kind or "материал")
    lesson = ""
    if lesson_key:
        title = f" «{lesson_title}»" if lesson_title else ""
        lesson = f" · Урок {lesson_key}{title}"
    loc = ""
    if start_sec is not None:
        loc = f" · {_fmt_mmss(start_sec)}–{_fmt_mmss(end_sec if end_sec is not None else start_sec)}"
    elif page is not None:
        loc = f" · стр. {int(page)}"
    return f"[{product_name}{lesson} · {kind_l}{loc}]"


def _fmt_mmss(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 60}:{s % 60:02d}"


def v2_base_metadata(
    *,
    product_id: str,
    source_id: str,
    source_kind: str,
    origin: str,
    lesson_key: str = "",
    module_no: Optional[int] = None,
    recorded_on: str = "",
    schema_v: int = 2,
    legacy: bool = False,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "schema_v": int(schema_v),
        "product_id": product_id,
        "legacy": bool(legacy),
        "source_id": str(source_id),
        "source_kind": source_kind,
        "origin": origin,
        "lesson_key": lesson_key or "",
    }
    if module_no is not None:
        meta["module_no"] = int(module_no)
    if recorded_on:
        meta["recorded_on"] = recorded_on
    return meta


class MaterialIndexService:
    def __init__(self, store: VectorStoreService):
        self._store = store

    @property
    def settings(self) -> RAGSettings:
        return self._store.settings

    def add_material_text(
        self,
        full_text: str,
        *,
        base_metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        dedupe_salt: str = "",
        heading: str = "",
        pages: Optional[Sequence[tuple[int, str]]] = None,
    ) -> tuple[int, List[str]]:
        """
        Чанкует текст (по предложениям, без разрыва) и добавляет в expert_materials.
        """
        base_metadata = dict(base_metadata or {})
        base_metadata.setdefault("source", source)

        pieces: List[tuple[Optional[int], str]] = []
        if pages:
            for page_no, page_text in pages:
                pieces.append((page_no, page_text or ""))
        else:
            pieces.append((None, full_text or ""))

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        idx = 0
        for page_no, raw in pieces:
            sents = split_sentences(raw)
            chunks = pack_sentences(
                sents,
                chunk_size=self.settings.chunk_size_tokens,
                overlap_sentences=2,
                encoding_name=self.settings.tiktoken_encoding,
            )
            if not chunks and (raw or "").strip():
                chunks = [(raw or "").strip()]
            for ch in chunks:
                body = f"{heading}\n{ch}".strip() if heading else ch
                page_heading = ""
                if page_no is not None and not heading:
                    page_heading = f"[стр. {page_no}]\n"
                    body = page_heading + ch
                elif page_no is not None and heading and "стр." not in heading:
                    body = heading.replace("]", f" · стр. {page_no}]") + "\n" + ch
                chunk_id = dedupe_material_key(source, body, idx, dedupe_salt=dedupe_salt)
                if self.has_chunk_id(chunk_id):
                    idx += 1
                    continue
                meta = dict(base_metadata)
                meta["chunk_index"] = idx
                if page_no is not None:
                    meta["page"] = int(page_no)
                ids.append(chunk_id)
                documents.append(body)
                metadatas.append(normalize_chroma_metadata(meta))
                idx += 1

        if not ids:
            return 0, []
        try:
            self._store.expert_collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            return len(ids), ids
        except Exception as e:
            logger.error("expert add failed: %s", e, exc_info=True)
            return 0, []

    async def add_material_text_async(
        self,
        full_text: str,
        *,
        base_metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        dedupe_salt: str = "",
        heading: str = "",
        pages: Optional[Sequence[tuple[int, str]]] = None,
    ) -> tuple[int, List[str]]:
        return await asyncio.to_thread(
            self.add_material_text,
            full_text,
            base_metadata=base_metadata,
            source=source,
            dedupe_salt=dedupe_salt,
            heading=heading,
            pages=pages,
        )

    def add_segments_text(
        self,
        segments: Sequence[Dict[str, Any]],
        *,
        base_metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        dedupe_salt: str = "",
        heading_fn=None,
        window_sec: float = 150.0,
        max_tokens: int = 600,
    ) -> tuple[int, List[str]]:
        """Видео: окна по времени ~2–3 мин / ~600 токенов с таймкодами в тексте."""
        from rag.chunking import get_encoder

        base_metadata = dict(base_metadata or {})
        base_metadata.setdefault("source", source)
        enc = get_encoder(self.settings.tiktoken_encoding)
        rows = [
            s
            for s in segments
            if str(s.get("text") or "").strip()
        ]
        if not rows:
            return 0, []

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        i = 0
        n = len(rows)
        idx = 0
        while i < n:
            start = float(rows[i].get("start") or 0)
            texts: List[str] = []
            tok = 0
            j = i
            end = start
            while j < n:
                t = str(rows[j].get("text") or "").strip()
                end = float(rows[j].get("end") or rows[j].get("start") or end)
                add = len(enc.encode(t))
                if texts and (end - start > window_sec or tok + add > max_tokens):
                    break
                texts.append(t)
                tok += add
                j += 1
            if not texts:
                i += 1
                continue
            heading = ""
            if heading_fn:
                heading = heading_fn(start, end)
            body = (("\n".join(filter(None, [heading, " ".join(texts)]))).strip())
            chunk_id = dedupe_material_key(source, body, idx, dedupe_salt=dedupe_salt)
            if not self.has_chunk_id(chunk_id):
                meta = dict(base_metadata)
                meta["chunk_index"] = idx
                meta["start_sec"] = float(start)
                ids.append(chunk_id)
                documents.append(body)
                metadatas.append(normalize_chroma_metadata(meta))
            idx += 1
            i = max(j, i + 1)

        if not ids:
            return 0, []
        try:
            self._store.expert_collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            return len(ids), ids
        except Exception as e:
            logger.error("add_segments_text failed: %s", e, exc_info=True)
            return 0, []

    async def add_segments_text_async(self, *args, **kwargs) -> tuple[int, List[str]]:
        return await asyncio.to_thread(self.add_segments_text, *args, **kwargs)

    def delete_by_source(self, source_id: str) -> int:
        sid = str(source_id or "").strip()
        if not sid:
            return 0
        try:
            before = self._store.expert_collection.get(
                where={"source_id": sid}, include=[]
            )
            ids = (before.get("ids") or []) if before else []
            if ids:
                self._store.expert_collection.delete(ids=ids)
            try:
                cards = self._store.cards_collection.get(
                    where={"source_id": sid}, include=[]
                )
                cids = (cards.get("ids") or []) if cards else []
                if cids:
                    self._store.cards_collection.delete(ids=cids)
            except Exception:
                pass
            return len(ids)
        except Exception as e:
            logger.error("delete_by_source %s: %s", sid, e)
            try:
                self._store.expert_collection.delete(where={"source_id": sid})
            except Exception:
                return 0
            return -1

    def add_card_document(
        self,
        *,
        card_id: str,
        title: str,
        text: str,
        metadata: Dict[str, Any],
    ) -> bool:
        doc = f"{(title or '').strip()}\n{(text or '').strip()}".strip()
        if not doc:
            return False
        meta = normalize_chroma_metadata({**metadata, "card_id": str(card_id)})
        try:
            existing = self._store.cards_collection.get(ids=[str(card_id)])
            if existing and existing.get("ids"):
                self._store.cards_collection.update(
                    ids=[str(card_id)],
                    documents=[doc],
                    metadatas=[meta],
                )
            else:
                self._store.cards_collection.add(
                    ids=[str(card_id)],
                    documents=[doc],
                    metadatas=[meta],
                )
            return True
        except Exception as e:
            logger.error("add_card_document: %s", e)
            return False

    def has_chunk_id(self, chunk_id: str) -> bool:
        try:
            r = self._store.expert_collection.get(ids=[chunk_id])
            return bool(r and r.get("ids"))
        except Exception:
            return False
