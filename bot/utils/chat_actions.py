"""Выбор chat action под тип входящего сообщения (медиапроцессор)."""

from aiogram import Bot
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender


def media_processing_chat_action(bot: Bot, message: Message) -> ChatActionSender:
    """
    Статус в чате, пока идёт распознавание/скачивание медиа.
    Голос/аудио — «записывает голос»; фото — загрузка фото; и т.д.
    """
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    if message.voice:
        return ChatActionSender.record_voice(chat_id, bot, thread_id)
    if message.audio:
        return ChatActionSender.upload_voice(chat_id, bot, thread_id)
    if message.video:
        return ChatActionSender.upload_video(chat_id, bot, thread_id)
    if message.video_note:
        return ChatActionSender.record_video_note(chat_id, bot, thread_id)
    if message.photo:
        return ChatActionSender.upload_photo(chat_id, bot, thread_id)
    if message.document:
        return ChatActionSender.upload_document(chat_id, bot, thread_id)
    if message.sticker:
        return ChatActionSender.choose_sticker(chat_id, bot, thread_id)
    if message.location or message.venue:
        return ChatActionSender.find_location(chat_id, bot, thread_id)
    if message.animation:
        return ChatActionSender.upload_video(chat_id, bot, thread_id)

    return ChatActionSender.typing(chat_id, bot, thread_id)
