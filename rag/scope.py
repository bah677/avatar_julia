"""Шлюз чтения из Chroma: каждый запрос обязан иметь область продукта."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from rag.types import format_retrieval_line

logger = logging.getLogger(__name__)

EXPERT_ID = "_expert"


class ScopeRequiredError(ValueError):
    """Запрос к Chroma без ProductScope."""


@dataclass(frozen=True)
class ProductScope:
    product_id: str
    include_expert: bool = True
    include_legacy: bool = True

    def product_ids(self) -> List[str]:
        pid = (self.product_id or "").strip()
        if not pid:
            raise ScopeRequiredError("ProductScope.product_id пуст")
        ids = [pid]
        if self.include_expert and EXPERT_ID not in ids:
            ids.append(EXPERT_ID)
        return ids

    def where(self, extra: dict | None = None) -> dict:
        """{"product_id": {"$in": [...]}} + {"legacy": {"$ne": True}} + extra, через $and."""
        clauses: List[dict] = [{"product_id": {"$in": self.product_ids()}}]
        if not self.include_legacy:
            clauses.append({"legacy": {"$ne": True}})
        if extra:
            clauses.append(extra)
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}


def _has_product_filter(where: Optional[dict]) -> bool:
    if not isinstance(where, dict) or not where:
        return False
    if "product_id" in where:
        return True
    inner = where.get("$and") or where.get("$or") or []
    if isinstance(inner, list):
        return any(_has_product_filter(x) for x in inner if isinstance(x, dict))
    return False


class RagScope:
    """Единственная точка чтения из Chroma для фич."""

    def __init__(self, store: VectorStoreService, scope: ProductScope):
        if scope is None:
            raise ScopeRequiredError("нужен ProductScope")
        self._store = store
        self.scope = scope

    def _query(
        self,
        collection,
        query: str,
        *,
        k: int,
        extra: Optional[dict] = None,
    ) -> Dict[str, Any]:
        from rag.truncate import truncate_for_embedding

        q = truncate_for_embedding((query or "").strip())
        if not q:
            return {}
        where = self.scope.where(extra)
        if not _has_product_filter(where):
            raise ScopeRequiredError("фильтр product_id обязателен")
        try:
            return collection.query(
                query_texts=[q],
                n_results=max(1, int(k)),
                where=where,
            )
        except Exception as e:
            logger.error("RagScope.query failed: %s", e, exc_info=True)
            return {}

    def search_chunks(
        self,
        query: str,
        k: int = 5,
        *,
        lesson_key: Optional[str] = None,
        kinds: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        extra: Dict[str, Any] | None = None
        clauses: List[dict] = []
        if lesson_key:
            clauses.append({"lesson_key": str(lesson_key)})
        if kinds:
            klist = [str(x) for x in kinds if str(x).strip()]
            if klist:
                clauses.append({"source_kind": {"$in": klist}})
        if len(clauses) == 1:
            extra = clauses[0]
        elif len(clauses) > 1:
            extra = {"$and": clauses}
        raw = self._query(self._store.expert_collection, query, k=k, extra=extra)
        return _hits_from_query(raw)

    def search_cards(
        self,
        query: str,
        k: int = 5,
        *,
        lesson_key: Optional[str] = None,
        types: Optional[Sequence[str]] = None,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        clauses: List[dict] = []
        if status:
            clauses.append({"status": status})
        if lesson_key:
            clauses.append({"lesson_key": str(lesson_key)})
        if types:
            tlist = [str(x) for x in types if str(x).strip()]
            if tlist:
                clauses.append({"type": {"$in": tlist}})
        extra = clauses[0] if len(clauses) == 1 else ({"$and": clauses} if clauses else None)
        coll = self._store.cards_collection
        raw = self._query(coll, query, k=k, extra=extra)
        return _hits_from_query(raw)

    def golden_examples(
        self,
        query: str,
        fmt: str,
        k: int = 2,
    ) -> List[Dict[str, Any]]:
        extra = {"format": str(fmt)} if fmt else None
        raw = self._query(self._store.golden_collection, query, k=k, extra=extra)
        hits = _hits_from_query(raw)
        if hits or not fmt:
            return hits
        # fallback: тот же формат в общем слое уже в $in; если пусто — без format
        raw2 = self._query(self._store.golden_collection, query, k=k, extra=None)
        return _hits_from_query(raw2)

    def format_chunks_for_prompt(self, hits: List[Dict[str, Any]], *, max_per_source: int = 2) -> str:
        from rag.chunking import jaccard_shingles

        picked: List[Dict[str, Any]] = []
        per_source: Dict[str, int] = {}
        for hit in hits:
            meta = hit.get("metadata") or {}
            sid = str(meta.get("source_id") or meta.get("source") or "")
            if sid:
                if per_source.get(sid, 0) >= max_per_source:
                    continue
            doc = str(hit.get("document") or "")
            if any(
                jaccard_shingles(doc, str(p.get("document") or "")) > 0.6
                for p in picked
            ):
                continue
            picked.append(hit)
            if sid:
                per_source[sid] = per_source.get(sid, 0) + 1
        lines = [
            format_retrieval_line(h.get("metadata") or {}, str(h.get("document") or ""))
            for h in picked
            if str(h.get("document") or "").strip()
        ]
        return "\n\n".join(lines)


def _hits_from_query(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    ids_list = raw.get("ids") or []
    docs_list = raw.get("documents") or []
    meta_list = raw.get("metadatas") or []
    dist_list = raw.get("distances") or []
    if not ids_list or not ids_list[0]:
        return []
    docs = docs_list[0] if docs_list else []
    metas = meta_list[0] if meta_list else []
    dists = dist_list[0] if dist_list else []
    out: List[Dict[str, Any]] = []
    for i, cid in enumerate(ids_list[0]):
        out.append(
            {
                "id": cid,
                "document": docs[i] if i < len(docs) else "",
                "metadata": metas[i] if i < len(metas) else {},
                "distance": dists[i] if i < len(dists) else None,
            }
        )
    return out


def active_scope() -> ProductScope:
    from config import config
    from course.products import active_product_id

    return ProductScope(
        product_id=active_product_id(),
        include_expert=True,
        include_legacy=bool(getattr(config, "COURSE_USE_LEGACY_CHUNKS", True)),
    )


def scope_from_stack(rag_stack, scope: Optional[ProductScope] = None) -> RagScope:
    if rag_stack is None:
        raise RuntimeError("RAG не поднят")
    return RagScope(rag_stack.vectors, scope or active_scope())
