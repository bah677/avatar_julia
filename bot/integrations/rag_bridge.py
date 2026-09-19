"""
Мост между ``config.AppConfig`` и пакетом ``rag`` (без логики RAG).

В другом проекте замените на свою фабрику: главное — собрать ``RAGSettings`` и вызвать
``build_rag_stack``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from rag.runtime import RagStack, build_rag_stack
from rag.settings import RAGSettings

if TYPE_CHECKING:
    from config import AppConfig

logger = logging.getLogger(__name__)


def try_build_rag_stack(cfg: "AppConfig") -> Optional[RagStack]:
    """
    Если RAG выключен или нет ключа OpenAI — возвращает None.
    Иначе поднимает Chroma в ``CHROMA_PERSIST_DIR``.
    """
    if not cfg.RAG_ENABLED:
        logger.info("RAG_DISABLED: пропуск инициализации Chroma")
        return None
    key = (cfg.OPENAI_API_KEY or "").strip()
    if not key:
        logger.warning("RAG_ENABLED без OPENAI_API_KEY — Chroma не поднимаем")
        return None

    settings = RAGSettings(
        openai_api_key=key,
        persist_directory=str(cfg.resolved_chroma_persist_dir),
        expert_collection_name=cfg.RAG_EXPERT_COLLECTION,
        golden_collection_name=cfg.RAG_GOLDEN_COLLECTION,
        embedding_model=cfg.RAG_EMBEDDING_MODEL,
        chunk_size_tokens=cfg.RAG_CHUNK_SIZE_TOKENS,
        chunk_overlap_tokens=cfg.RAG_CHUNK_OVERLAP_TOKENS,
        tiktoken_encoding=cfg.RAG_TIKTOKEN_ENCODING,
        cards_collection_name=getattr(cfg, "RAG_CARDS_COLLECTION", "content_cards"),
    )
    stack = build_rag_stack(settings)
    ne, ng = stack.expert_count_golden_count()
    logger.info("RAG stack ready (expert_chunks≈%s, golden≈%s)", ne, ng)
    return stack
