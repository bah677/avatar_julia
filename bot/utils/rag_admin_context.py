"""Админская группа/ветка RAG: уведомления, мастер догрузки."""

from __future__ import annotations

from typing import Optional, Tuple

from config import config


def normalize_supergroup_chat_id(raw: int) -> int:
    """``3756916561`` → ``-1003756916561``."""
    n = int(raw or 0)
    if n == 0:
        return 0
    if n > 0:
        s = str(n)
        if not s.startswith("100") and len(s) >= 9:
            return int(f"-100{n}")
    return n


def rag_admin_chat_id() -> int:
    return int(getattr(config, "rag_admin_chat_id", 0) or 0)


def rag_admin_topic_id() -> Optional[int]:
    tid = int(getattr(config, "rag_admin_topic_id", 0) or 0)
    return tid if tid else None


def rag_admin_chat_topic() -> Tuple[int, Optional[int]]:
    """(chat_id, message_thread_id | None) для send_message."""
    return rag_admin_chat_id(), rag_admin_topic_id()


def is_rag_admin_message(chat_id: int, thread_id: Optional[int]) -> bool:
    admin_chat = rag_admin_chat_id()
    if not admin_chat or int(chat_id) != admin_chat:
        return False
    admin_topic = rag_admin_topic_id()
    if admin_topic is None:
        return True
    return thread_id is not None and int(thread_id) == admin_topic


def rag_shorts_chat_topic() -> Tuple[int, Optional[int]]:
    chat = int(getattr(config, "rag_shorts_chat_id", 0) or 0)
    tid = int(getattr(config, "RAG_SHORTS_TOPIC_ID", 0) or 0)
    return chat, tid if tid else None


def is_rag_shorts_message(chat_id: int, thread_id: Optional[int]) -> bool:
    shorts_chat, shorts_topic = rag_shorts_chat_topic()
    if not shorts_chat or int(chat_id) != shorts_chat:
        return False
    if shorts_topic is None:
        return True
    return thread_id is not None and int(thread_id) == shorts_topic
