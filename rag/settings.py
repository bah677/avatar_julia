"""Параметры RAG: только dataclass, без чтения env (хост передаёт значения)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RAGSettings:
    """Все обязательные для старта Chroma поля задаёт вызывающий код."""

    openai_api_key: str
    persist_directory: str = "./chroma_data"

    expert_collection_name: str = "expert_materials"
    golden_collection_name: str = "golden_examples"
    cards_collection_name: str = "content_cards"

    embedding_model: str = "text-embedding-3-small"

    #: Целевой размер чанка и перекрытие (ТЗ: 500–800 токенов, overlap 100).
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 100

    #: Подбор кодировки для tiktoken (для моделей без отдельного encoding).
    tiktoken_encoding: str = "cl100k_base"
