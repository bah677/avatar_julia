"""Фабрика embedding function для Chroma (OpenAI). Отдельно от остального кода."""

from __future__ import annotations

from chromadb.utils import embedding_functions

from rag.truncate import truncate_for_embedding

__all__ = ["truncate_for_embedding", "make_openai_embedding_function"]


def make_openai_embedding_function(
    *,
    api_key: str,
    model_name: str = "text-embedding-3-small",
):
    if not api_key or not str(api_key).strip():
        raise ValueError("openai_api_key is required for embeddings")
    return embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key.strip(),
        model_name=model_name,
    )
