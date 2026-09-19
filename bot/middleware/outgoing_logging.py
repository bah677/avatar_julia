"""Session-middleware для логирования всех исходящих сообщений бота.

Подключается к `Bot.session.middleware` и срабатывает на каждый вызов
Telegram Bot API. Если ответ — это `Message` (или список `Message`),
сохраняет его в таблицу messages через MessageCopier.save_outgoing.

Это даёт:
  * полное логирование ответов агента (сейчас вообще не логируется);
  * автоматическое логирование рассылок;
  * ответы саппорта/админ-фичи;
  * любые системные сообщения (welcome, лицензия, payment).

Бот не знает о middleware — она прозрачна для прикладного кода.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods.base import TelegramType
from aiogram.types import Message

if TYPE_CHECKING:
    from aiogram.client.bot import Bot
    from aiogram.methods import Response, TelegramMethod

    from bot.logging.message_copier import MessageCopier

logger = logging.getLogger(__name__)


# Имена aiogram-методов, которые возвращают Message и которые мы хотим логировать.
_OUTGOING_METHODS = {
    "SendMessage",
    "SendPhoto",
    "SendAudio",
    "SendVoice",
    "SendVideo",
    "SendVideoNote",
    "SendDocument",
    "SendSticker",
    "SendAnimation",
    "SendDice",
    "SendLocation",
    "SendVenue",
    "SendContact",
    "SendPoll",
    "ForwardMessage",
    "CopyMessage",
    "EditMessageText",
    "EditMessageCaption",
    "EditMessageMedia",
}


def _method_to_message_type(method_name: str) -> str:
    return {
        "SendMessage": "text",
        "SendPhoto": "photo",
        "SendAudio": "audio",
        "SendVoice": "voice",
        "SendVideo": "video",
        "SendVideoNote": "video_note",
        "SendDocument": "document",
        "SendSticker": "sticker",
        "SendAnimation": "animation",
        "SendDice": "dice",
        "SendLocation": "location",
        "SendVenue": "venue",
        "SendContact": "contact",
        "SendPoll": "poll",
        "ForwardMessage": "forward",
        "CopyMessage": "copy",
        "EditMessageText": "edit_text",
        "EditMessageCaption": "edit_caption",
        "EditMessageMedia": "edit_media",
    }.get(method_name, "text")


class OutgoingLoggingMiddleware(BaseRequestMiddleware):
    """Перехватывает успешные ответы от Telegram API и пишет в messages."""

    def __init__(self, message_copier: "MessageCopier", default_source: str = "bot"):
        super().__init__()
        self.message_copier = message_copier
        self.default_source = default_source

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: "Bot",
        method: "TelegramMethod[TelegramType]",
    ) -> "Response[TelegramType]":
        response = await make_request(bot, method)
        try:
            await self._log(method, response)
        except Exception as e:  # noqa: BLE001
            # Логирование ни в коем случае не должно ломать отправку.
            logger.error("OutgoingLoggingMiddleware failed: %s", e, exc_info=True)
        return response

    async def _log(self, method: Any, response: Any) -> None:
        method_name = type(method).__name__
        if method_name not in _OUTGOING_METHODS:
            return

        result = getattr(response, "result", response)
        # Нормализуем к списку Message.
        if isinstance(result, Message):
            messages = [result]
        elif isinstance(result, list) and result and isinstance(result[0], Message):
            messages = result
        else:
            return

        message_type = _method_to_message_type(method_name)
        # Source/subtype — стараемся выудить из метода что-то полезное.
        # На исходящем уровне у нас нет богатого контекста; фичи могут переопределить
        # логирование, явно вызвав save_outgoing(source=...).
        source = self.default_source

        for m in messages:
            if not getattr(m, "chat", None):
                continue
            await self.message_copier.save_outgoing(
                message=m,
                message_type=message_type,
                source=source,
            )
