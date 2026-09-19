"""Инициализация Chroma PersistentClient и коллекций."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import chromadb

from rag.embeddings import make_openai_embedding_function
from rag.settings import RAGSettings

logger = logging.getLogger(__name__)


class VectorStoreService:
    """Обертка над ChromaDB: клиент + коллекции с общей embedding function."""

    def __init__(self, settings: RAGSettings):
        self._settings = settings
        self._ef = make_openai_embedding_function(
            api_key=settings.openai_api_key,
            model_name=settings.embedding_model,
        )
        path = Path(settings.persist_directory)
        path.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(path))
        self._expert = self._client.get_or_create_collection(
            name=settings.expert_collection_name,
            embedding_function=self._ef,
            metadata={"description": "expert_materials"},
        )
        self._golden = self._ensure_cosine_collection(
            settings.golden_collection_name,
            description="golden_examples",
            recreate_if_empty=True,
        )
        self._cards = self._ensure_cosine_collection(
            settings.cards_collection_name,
            description="content_cards",
            recreate_if_empty=False,
        )
        logger.info(
            "Chroma ready at %s (%s, %s, %s)",
            path,
            settings.expert_collection_name,
            settings.golden_collection_name,
            settings.cards_collection_name,
        )

    def _ensure_cosine_collection(
        self,
        name: str,
        *,
        description: str,
        recreate_if_empty: bool,
    ):
        meta = {"description": description, "hnsw:space": "cosine"}
        try:
            coll = self._client.get_collection(name=name, embedding_function=self._ef)
            if recreate_if_empty:
                try:
                    n = coll.count()
                except Exception:
                    n = -1
                if n == 0:
                    self._client.delete_collection(name)
                    coll = self._client.create_collection(
                        name=name,
                        embedding_function=self._ef,
                        metadata=meta,
                    )
            return coll
        except Exception:
            return self._client.get_or_create_collection(
                name=name,
                embedding_function=self._ef,
                metadata=meta,
            )

    @property
    def client(self) -> chromadb.PersistentClient:
        return self._client

    @property
    def expert_collection(self):
        return self._expert

    @property
    def golden_collection(self):
        return self._golden

    @property
    def cards_collection(self):
        return self._cards

    @property
    def settings(self) -> RAGSettings:
        return self._settings

    def collection_count(self) -> tuple[int, int]:
        """(expert_count, golden_count) — для health-check."""
        try:
            ne = self._expert.count()
        except Exception:
            ne = -1
        try:
            ng = self._golden.count()
        except Exception:
            ng = -1
        return ne, ng

    def cards_count(self) -> int:
        try:
            return self._cards.count()
        except Exception:
            return -1

    def reset_all_collections(self) -> None:
        expert_name = self._settings.expert_collection_name
        golden_name = self._settings.golden_collection_name
        cards_name = self._settings.cards_collection_name
        for name in (expert_name, golden_name, cards_name):
            try:
                self._client.delete_collection(name)
                logger.info("Chroma collection deleted: %s", name)
            except Exception as e:
                logger.warning("Chroma delete_collection %s: %s", name, e)
        self._expert = self._client.get_or_create_collection(
            name=expert_name,
            embedding_function=self._ef,
            metadata={"description": "expert_materials"},
        )
        self._golden = self._client.get_or_create_collection(
            name=golden_name,
            embedding_function=self._ef,
            metadata={"description": "golden_examples", "hnsw:space": "cosine"},
        )
        self._cards = self._client.get_or_create_collection(
            name=cards_name,
            embedding_function=self._ef,
            metadata={"description": "content_cards", "hnsw:space": "cosine"},
        )
        logger.info(
            "Chroma collections recreated empty: %s, %s, %s",
            expert_name,
            golden_name,
            cards_name,
        )
