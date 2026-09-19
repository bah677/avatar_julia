"""
Вызов Bot API ``getForumTopic``.

В ряде версий aiogram класс ``GetForumTopic`` отсутствует в ``aiogram.methods``,
хотя метод поддерживается Telegram — держим минимальную обёртку ``TelegramMethod``.
"""

from __future__ import annotations

from aiogram.methods.base import TelegramMethod
from aiogram.types import ChatIdUnion, ForumTopic


class GetForumTopic(TelegramMethod[ForumTopic]):
    __returning__ = ForumTopic
    __api_method__ = "getForumTopic"

    chat_id: ChatIdUnion
    message_thread_id: int
