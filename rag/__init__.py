"""
Изолированный слой RAG (Chroma + эмбеддинги + retrieval + золотой фонд).

Тяжёлые зависимости (chromadb) подтягиваются лениво при обращении к runtime/store.
"""

from rag.settings import RAGSettings

__all__ = [
    "RAGSettings",
    "RagStack",
    "build_rag_stack",
    "VectorStoreService",
    "ExpertRetriever",
    "GoldenExamplesStore",
    "MaterialIndexService",
]


def __getattr__(name: str):
    if name in ("RagStack", "build_rag_stack"):
        from rag.runtime import RagStack, build_rag_stack

        return {"RagStack": RagStack, "build_rag_stack": build_rag_stack}[name]
    if name == "VectorStoreService":
        from rag.vector_store import VectorStoreService

        return VectorStoreService
    if name == "ExpertRetriever":
        from rag.retriever import ExpertRetriever

        return ExpertRetriever
    if name == "GoldenExamplesStore":
        from rag.golden_store import GoldenExamplesStore

        return GoldenExamplesStore
    if name == "MaterialIndexService":
        from rag.material_index import MaterialIndexService

        return MaterialIndexService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
