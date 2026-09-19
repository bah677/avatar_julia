"""Семантический поиск по коллекции expert_materials → текст для промпта."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from rag.retrieval_planner import (
    CONTENT_CATEGORY_TESTIMONIAL,
    RetrievalPlan,
    build_dialogue_context_text,
    plan_retrieval_async,
)
from rag.embeddings import truncate_for_embedding
from rag.types import format_retrieval_line, format_retrieval_sections
from rag.vector_store import VectorStoreService

logger = logging.getLogger(__name__)

_WHERE_EXCLUDE_TESTIMONIAL: Dict[str, Any] = {
    "content_category": {"$ne": CONTENT_CATEGORY_TESTIMONIAL},
}
_WHERE_ONLY_TESTIMONIAL: Dict[str, Any] = {
    "content_category": CONTENT_CATEGORY_TESTIMONIAL,
}


class ExpertRetriever:
    """Вызовы Chroma синхронные; публичные ``async`` методы гоняют их в thread pool."""

    def __init__(self, store: VectorStoreService):
        self._store = store

    def retrieve_context(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Аналог ТЗ: ``collection.query`` + склейка блоков ``[source - tags]: chunk``.
        ``where`` — фильтр Chroma (metadata), или None.
        """
        q = truncate_for_embedding((query or "").strip())
        if not q:
            return ""

        try:
            raw = self._query_raw(q, top_k=top_k, where=where)
        except Exception as e:
            logger.error("expert collection query failed: %s", e, exc_info=True)
            return ""

        return self._format_query_results(raw)

    async def retrieve_context_async(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> str:
        return await asyncio.to_thread(
            self.retrieve_context, query, top_k=top_k, where=where
        )

    def _query_raw(
        self,
        query: str,
        *,
        top_k: int,
        where: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        from rag.scope import RagScope, active_scope

        q = truncate_for_embedding((query or "").strip())
        if not q:
            return {}
        gateway = RagScope(self._store, active_scope())
        return gateway._query(
            self._store.expert_collection, query, k=top_k, extra=where
        )

    def retrieve_merged_for_task(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        n_filtered: int = 5,
        n_broad: int = 3,
        max_chunks: int = 6,
    ) -> str:
        """
        Два запроса в expert_materials: с фильтром (product ИЛИ content_type) и без;
        склейка без дубликатов по id, порядок — сначала целевые, затем общие, обрезка.
        """
        q = truncate_for_embedding((query or "").strip())
        if not q:
            return ""

        prod = (product or "").strip()
        ctype = (content_type or "").strip()
        seen: set[str] = set()
        merged_docs: List[str] = []
        merged_meta: List[Dict[str, Any]] = []

        def _ingest(raw: Dict[str, Any]) -> None:
            if not raw:
                return
            ids_list = raw.get("ids") or []
            docs_list = raw.get("documents") or []
            meta_list = raw.get("metadatas") or []
            if not ids_list or not ids_list[0]:
                return
            for cid, doc, meta in zip(ids_list[0], docs_list[0], meta_list[0]):
                if not cid or cid in seen:
                    continue
                if not doc or not str(doc).strip():
                    continue
                seen.add(cid)
                merged_docs.append(str(doc).strip())
                merged_meta.append(meta or {})
                if len(merged_docs) >= max_chunks:
                    return

        try:
            if prod and ctype:
                w = {
                    "$and": [
                        {"$or": [{"product": prod}, {"content_type": ctype}]},
                        _WHERE_EXCLUDE_TESTIMONIAL,
                    ]
                }
                raw_f = self._query_raw(q, top_k=n_filtered, where=w)
                _ingest(raw_f)
            raw_b = self._query_raw(q, top_k=n_broad, where=_WHERE_EXCLUDE_TESTIMONIAL)
            _ingest(raw_b)
        except Exception as e:
            logger.error("retrieve_merged_for_task failed: %s", e, exc_info=True)
            return ""

        lines: List[str] = []
        for doc, meta in zip(merged_docs, merged_meta):
            lines.append(format_retrieval_line(meta or {}, doc))

        return "\n\n".join(lines)

    async def retrieve_merged_for_task_async(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        n_filtered: int = 5,
        n_broad: int = 3,
        max_chunks: int = 6,
    ) -> str:
        return await asyncio.to_thread(
            self.retrieve_merged_for_task,
            query,
            product=product,
            content_type=content_type,
            n_filtered=n_filtered,
            n_broad=n_broad,
            max_chunks=max_chunks,
        )

    def _merge_chunks(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        n_filtered: int,
        n_broad: int,
        max_chunks: int,
        base_where: Optional[Dict[str, Any]],
    ) -> str:
        q = truncate_for_embedding((query or "").strip())
        if not q:
            return ""

        prod = (product or "").strip()
        ctype = (content_type or "").strip()
        seen: set[str] = set()
        merged_docs: List[str] = []
        merged_meta: List[Dict[str, Any]] = []

        def _ingest(raw: Dict[str, Any]) -> None:
            if not raw:
                return
            ids_list = raw.get("ids") or []
            docs_list = raw.get("documents") or []
            meta_list = raw.get("metadatas") or []
            if not ids_list or not ids_list[0]:
                return
            for cid, doc, meta in zip(ids_list[0], docs_list[0], meta_list[0]):
                if not cid or cid in seen:
                    continue
                if not doc or not str(doc).strip():
                    continue
                seen.add(cid)
                merged_docs.append(str(doc).strip())
                merged_meta.append(meta or {})
                if len(merged_docs) >= max_chunks:
                    return

        try:
            if prod and ctype:
                w: Dict[str, Any] = {
                    "$and": [
                        {"$or": [{"product": prod}, {"content_type": ctype}]},
                    ]
                }
                if base_where:
                    w["$and"].append(base_where)
                raw_f = self._query_raw(q, top_k=n_filtered, where=w)
                _ingest(raw_f)
            where_broad = base_where
            raw_b = self._query_raw(q, top_k=n_broad, where=where_broad)
            _ingest(raw_b)
        except Exception as e:
            logger.error("_merge_chunks failed: %s", e, exc_info=True)
            return ""

        lines: List[str] = []
        for doc, meta in zip(merged_docs, merged_meta):
            lines.append(format_retrieval_line(meta or {}, doc))

        return "\n\n".join(lines)

    def retrieve_expert_for_task(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        n_filtered: int = 5,
        n_broad: int = 3,
        max_chunks: int = 5,
    ) -> str:
        return self._merge_chunks(
            query,
            product=product,
            content_type=content_type,
            n_filtered=n_filtered,
            n_broad=n_broad,
            max_chunks=max_chunks,
            base_where=_WHERE_EXCLUDE_TESTIMONIAL,
        )

    def retrieve_testimonials_for_task(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        n_filtered: int = 2,
        n_broad: int = 1,
        max_chunks: int = 2,
    ) -> str:
        return self._merge_chunks(
            query,
            product=product,
            content_type=content_type,
            n_filtered=n_filtered,
            n_broad=n_broad,
            max_chunks=max_chunks,
            base_where=_WHERE_ONLY_TESTIMONIAL,
        )

    async def retrieve_for_avatar_task_async(
        self,
        *,
        user_turns: List[str],
        task_summary: str = "",
        product: str = "",
        content_type: str = "",
        context_user_turns: int = 4,
        expert_max_chunks: int = 5,
        testimonial_max_chunks: int = 2,
        use_planner: bool = False,
        lesson_key: str = "",
    ) -> str:
        from rag.scope import RagScope, active_scope

        ctx = build_dialogue_context_text(
            user_turns,
            task_summary=task_summary,
            product=product,
            content_type=content_type,
            max_turns=context_user_turns,
        )
        if not ctx.strip():
            return ""

        def _run() -> str:
            scope = active_scope()
            gateway = RagScope(self._store, scope)
            extra = {"lesson_key": lesson_key} if lesson_key else None
            hits = gateway.search_chunks(
                ctx,
                k=expert_max_chunks,
                lesson_key=lesson_key or None,
            )
            return gateway.format_chunks_for_prompt(hits)

        return await asyncio.to_thread(_run)

    def distinct_expert_metadata_values(
        self,
        field: str,
        *,
        max_scan: int = 50_000,
        page_size: int = 2_000,
    ) -> List[str]:
        """
        Уникальные значения поля metadata в коллекции expert_materials
        (например ``content_type``, ``product``) — для кнопок /new.

        Читает чанки страницами; при большой базе ограничение ``max_scan`` защищает от долгого цикла.
        """
        f = (field or "").strip()
        if not f:
            return []

        coll = self._store.expert_collection
        seen: set[str] = set()
        offset = 0
        while offset < max_scan:
            try:
                raw = coll.get(
                    include=["metadatas"],
                    limit=page_size,
                    offset=offset,
                )
            except Exception as e:
                logger.error("Chroma get distinct %s: %s", f, e, exc_info=True)
                break
            metas = raw.get("metadatas") or []
            if not metas:
                break
            for m in metas:
                if not m:
                    continue
                if str(m.get("content_category") or "").strip() == CONTENT_CATEGORY_TESTIMONIAL:
                    continue
                v = m.get(f)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    seen.add(s)
            offset += len(metas)
            if len(metas) < page_size:
                break

        return sorted(seen)

    async def distinct_expert_metadata_values_async(
        self,
        field: str,
        *,
        max_scan: int = 50_000,
        page_size: int = 2_000,
    ) -> List[str]:
        return await asyncio.to_thread(
            self.distinct_expert_metadata_values,
            field,
            max_scan=max_scan,
            page_size=page_size,
        )

    def _format_query_results(self, raw: Dict[str, Any]) -> str:
        ids_list = raw.get("ids") or []
        docs_list = raw.get("documents") or []
        meta_list = raw.get("metadatas") or []

        if not ids_list or not ids_list[0]:
            return ""

        lines: List[str] = []
        for doc, meta in zip(docs_list[0], meta_list[0]):
            if not doc:
                continue
            lines.append(format_retrieval_line(meta or {}, doc.strip()))

        return "\n\n".join(lines)
