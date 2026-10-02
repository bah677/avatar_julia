"""Поиск в RAG при правке черновика."""

from __future__ import annotations

import unittest

from course.revision_rag import (
    retrieve_for_revision,
    revision_search_query,
    wants_client_voice,
)
from openai_client.content_prompts import writer_task_block
from rag.scope import ProductScope, RagScope
from tests.test_rag_scope import FakeStore, _product_ids_in


class RevisionQueryTests(unittest.TestCase):
    def test_query_puts_comment_first(self) -> None:
        q = revision_search_query(
            instruction="добавь цитаты участниц",
            task="пост про семью",
            card_titles=["Функциональная семья"],
        )
        self.assertTrue(q.startswith("добавь цитаты участниц"))
        self.assertIn("пост про семью", q)
        self.assertIn("Функциональная семья", q)

    def test_client_voice_heuristic(self) -> None:
        self.assertTrue(wants_client_voice("вставь живые цитаты участниц из зума"))
        self.assertFalse(wants_client_voice("сделай короче, убери воду"))


class RevisionRetrieveTests(unittest.TestCase):
    def test_searches_source_then_broad_then_testimonials(self) -> None:
        store = FakeStore()
        gw = RagScope(store, ProductScope(product_id="mbt"))
        text = retrieve_for_revision(
            gw,
            query="цитаты участниц зум 4",
            source_ids=["124dc585-369f-4cd7-838f-2b69c644be4b"],
            include_testimonials=True,
        )
        self.assertEqual(len(store.expert_collection.calls), 3)
        src_where = store.expert_collection.calls[0]["where"]
        self.assertIn("124dc585-369f-4cd7-838f-2b69c644be4b", str(src_where))
        self.assertIsNotNone(_product_ids_in(src_where))
        testi_where = store.expert_collection.calls[2]["where"]
        self.assertIn("testimonial", str(testi_where))
        self.assertTrue(text)

    def test_skips_testimonials_when_not_asked(self) -> None:
        store = FakeStore()
        gw = RagScope(store, ProductScope(product_id="mbt"))
        retrieve_for_revision(gw, query="сделай короче", include_testimonials=False)
        self.assertEqual(len(store.expert_collection.calls), 1)
        self.assertNotIn("testimonial", str(store.expert_collection.calls[0]["where"]))


class WriterPromptTests(unittest.TestCase):
    def test_retrieved_block_between_material_and_previous(self) -> None:
        block = writer_task_block(
            golden="",
            material="карточка",
            task="пост",
            previous="старый текст",
            instruction="добавь цитаты",
            retrieved="[практика]\nучастница: мне стало легче",
        )
        self.assertIn("## Из базы (по правке)", block)
        self.assertLess(block.find("## Из базы"), block.find("## Прошлая версия"))
        self.assertLess(block.find("## Прошлая версия"), block.find("## Правки"))


if __name__ == "__main__":
    unittest.main()
