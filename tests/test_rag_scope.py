"""Шлюз RAG: каждый запрос к Chroma обязан нести фильтр product_id."""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional

from rag.scope import ProductScope, RagScope, ScopeRequiredError


class FakeCollection:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "ids": [["id-1"]],
            "documents": [["chunk"]],
            "metadatas": [[{"product_id": "mbt"}]],
            "distances": [[0.1]],
        }


class FakeStore:
    def __init__(self) -> None:
        self.expert_collection = FakeCollection()
        self.cards_collection = FakeCollection()
        self.golden_collection = FakeCollection()


def _product_ids_in(where: Optional[dict]) -> Optional[list]:
    if not isinstance(where, dict):
        return None
    if "product_id" in where:
        val = where["product_id"]
        if isinstance(val, dict) and "$in" in val:
            return list(val["$in"])
        return [val]
    for key in ("$and", "$or"):
        inner = where.get(key) or []
        if isinstance(inner, list):
            for item in inner:
                found = _product_ids_in(item if isinstance(item, dict) else None)
                if found is not None:
                    return found
    return None


class RagScopeTests(unittest.TestCase):
    def test_scope_required(self) -> None:
        store = FakeStore()
        with self.assertRaises(ScopeRequiredError):
            RagScope(store, None)  # type: ignore[arg-type]

    def test_empty_product_id_raises(self) -> None:
        with self.assertRaises(ScopeRequiredError):
            ProductScope(product_id="").where()

    def test_search_chunks_always_filters_product_id(self) -> None:
        store = FakeStore()
        scope = ProductScope(product_id="mbt", include_expert=True, include_legacy=True)
        gateway = RagScope(store, scope)
        hits = gateway.search_chunks("тема урока", k=3)
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(store.expert_collection.calls), 1)
        where = store.expert_collection.calls[0]["where"]
        ids = _product_ids_in(where)
        self.assertIsNotNone(ids)
        self.assertEqual(ids, ["mbt", "_expert"])

    def test_search_cards_and_golden_filter_product(self) -> None:
        store = FakeStore()
        gateway = RagScope(store, ProductScope(product_id="mbt"))
        gateway.search_cards("боль", k=2, types=["pain"])
        gateway.golden_examples("пост", "tg_post", k=1)
        for coll in (store.cards_collection, store.golden_collection):
            self.assertGreaterEqual(len(coll.calls), 1)
            ids = _product_ids_in(coll.calls[0]["where"])
            self.assertIsNotNone(ids)
            self.assertIn("mbt", ids or [])
            self.assertIn("_expert", ids or [])

    def test_exclude_legacy_adds_clause(self) -> None:
        store = FakeStore()
        gateway = RagScope(
            store,
            ProductScope(product_id="mbt", include_legacy=False),
        )
        gateway.search_chunks("запрос", k=1)
        where = store.expert_collection.calls[0]["where"]
        blob = str(where)
        self.assertIn("legacy", blob)
        self.assertIn("$ne", blob)

    def test_empty_query_does_not_hit_chroma(self) -> None:
        store = FakeStore()
        gateway = RagScope(store, ProductScope(product_id="mbt"))
        self.assertEqual(gateway.search_chunks("   ", k=5), [])
        self.assertEqual(store.expert_collection.calls, [])


if __name__ == "__main__":
    unittest.main()
