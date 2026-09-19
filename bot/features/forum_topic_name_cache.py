"""
Кэш названий форум-топиков (chat_id, message_thread_id) → имя в PostgreSQL.

Раньше был процессный dict; теперь таблица ``forum_topic_names`` (см. ``012_forum_topic_names.sql``).

Telegram ``getForumTopic`` часто даёт 404; имя подставляется из ``forum_topic_created`` /
``forum_topic_edited`` и хранится в СУБД между перезапусками бота.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from storage.user_storage import UserStorage

Key = Tuple[int, int]


async def remember_forum_topic_name(
    storage: UserStorage,
    chat_id: int,
    message_thread_id: int,
    name: str,
) -> None:
    await storage.upsert_forum_topic_name(
        group_chat_id=chat_id,
        topic_id=message_thread_id,
        topic_name=name,
    )


async def get_cached_forum_topic_name(
    storage: UserStorage,
    chat_id: int,
    message_thread_id: int,
) -> Optional[str]:
    return await storage.get_forum_topic_name(chat_id, message_thread_id)


async def debug_cache_snapshot(
    storage: UserStorage,
    group_chat_id: int,
) -> Dict[Key, str]:
    """Все топики одной группы — для отладочных логов (как раньше dict по ключам)."""
    return await storage.forum_topic_names_snapshot_for_chat(group_chat_id)
