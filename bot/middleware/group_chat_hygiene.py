"""
Опциональная фича для «живого» клубного чата: приветствие при вступлении,
удаление служебных сообщений.

Подключайте middleware только когда в проекте есть группа (``CLUB_GROUP_ID``).
DM-only или бот без такого чата не регистрируют этот слой — логики нет на пути апдейтов.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Set

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ParseMode
from aiogram.types import Message, Update

from config import config

logger = logging.getLogger(__name__)


class GroupChatHygieneMiddleware(BaseMiddleware):
    SERVICE_MESSAGE_FIELDS: Set[str] = {
        "new_chat_members",
        "left_chat_member",
        "group_chat_created",
        "supergroup_chat_created",
        "channel_chat_created",
        "migrate_to_chat_id",
        "migrate_from_chat_id",
        "pinned_message",
        "forum_topic_created",
        "forum_topic_closed",
        "forum_topic_reopened",
        "forum_topic_edited",
        "forum_topic_pinned",
        "forum_topic_unpinned",
        "general_forum_topic_hidden",
        "general_forum_topic_unhidden",
        "write_allowed",
        "message_auto_delete_timer_changed",
    }

    def __init__(self, bot: Bot, club_group_id: int, welcome_topic_id: int):
        super().__init__()
        self.bot = bot
        self.club_group_id = club_group_id
        self.welcome_topic_id = welcome_topic_id
        logger.info(
            "GroupChatHygiene включён: club_group_id=%s welcome_topic_id=%s",
            club_group_id,
            welcome_topic_id,
        )

    def _is_service_message(self, message: Message) -> bool:
        for name in self.SERVICE_MESSAGE_FIELDS:
            if getattr(message, name, None):
                return True
        return False

    def _is_join_message(self, message: Message) -> bool:
        return bool(getattr(message, "new_chat_members", None))

    async def _handle_join_message(self, message: Message) -> None:
        try:
            new_members = message.new_chat_members
            if not new_members:
                return

            topic_id = self.welcome_topic_id or config.WELCOME_TOPIC_ID
            forum = bool(getattr(message.chat, "is_forum", False))

            # В форум-супергруппах без топика отправка почти всегда падает (Bad Request);
            # message_thread_id=0 тоже невалиден.
            if forum and not topic_id:
                logger.error(
                    "Группа %s объявлена как форум, но WELCOME_TOPIC_ID=0: "
                    "задайте id топика приветствий в .env (message_thread_id ветки из Telegram/Bot API).",
                    message.chat.id,
                )
                return

            for user in new_members:
                if user.is_bot:
                    logger.info("Bot %s joined, skipping welcome", user.id)
                    continue

                if user.username:
                    mention = f"@{user.username}"
                else:
                    name = user.full_name or f"Пользователь {user.id}"
                    mention = f"<a href='tg://user?id={user.id}'>{name}</a>"
                welcome_text = (
                    f"{mention}, рады тебя приветствовать в этом волшебном пространстве.\n\n"
                    f"В этой группе мы знакомимся и общаемся.\n\n"
                    f"Напиши как тебя зовут, из какого ты города, для чего ты здесь, "
                    f"как ты оказался здесь!"
                )

                kwargs: Dict[str, Any] = {
                    "chat_id": message.chat.id,
                    "text": welcome_text,
                    "parse_mode": ParseMode.HTML,
                }
                if topic_id:
                    kwargs["message_thread_id"] = topic_id

                await self.bot.send_message(**kwargs)
                logger.info(
                    "Welcome sent to user %s in chat %s thread=%s",
                    user.id,
                    message.chat.id,
                    topic_id or "(none)",
                )
        except Exception as e:
            logger.error("Failed to handle join message: %s", e, exc_info=True)

    async def __call__(self, handler, event: Update, data: Dict[str, Any]):
        if not event.message or event.message.chat.id != self.club_group_id:
            return await handler(event, data)

        msg = event.message

        if self._is_join_message(msg):
            await self._handle_join_message(msg)

        if self._is_service_message(msg):
            logger.info("Service message detected, deleting...")
            try:
                await msg.delete()
                logger.info(
                    "Deleted service message %s from chat %s",
                    msg.message_id,
                    msg.chat.id,
                )
            except Exception as e:
                logger.error("Failed to delete service message: %s", e)
            return

        return await handler(event, data)
