"""Сборка изолированного стека RAG для передачи в приложение."""

from __future__ import annotations

from dataclasses import dataclass

from rag.golden_store import GoldenExamplesStore
from rag.material_index import MaterialIndexService
from rag.retriever import ExpertRetriever
from rag.settings import RAGSettings
from rag.vector_store import VectorStoreService


@dataclass
class RagStack:
    """
    Все сервисы на одном Chroma-клиенте.

    В другом проекте: держите один экземпляр на процесс и пробрасывайте в DI/фичи.
    """

    settings: RAGSettings
    vectors: VectorStoreService
    retriever: ExpertRetriever
    golden: GoldenExamplesStore
    materials: MaterialIndexService

    def expert_count_golden_count(self) -> tuple[int, int]:
        return self.vectors.collection_count()

    def reset_all_vector_data(self) -> tuple[int, int]:
        """Полная очистка Chroma (материалы + golden). Синхронный вызов; из async — to_thread."""
        self.vectors.reset_all_collections()
        return self.vectors.collection_count()


def build_rag_stack(settings: RAGSettings) -> RagStack:
    """Создаёт каталог на диске при необходимости и обе коллекции."""
    vectors = VectorStoreService(settings)
    return RagStack(
        settings=settings,
        vectors=vectors,
        retriever=ExpertRetriever(vectors),
        golden=GoldenExamplesStore(vectors),
        materials=MaterialIndexService(vectors),
    )
