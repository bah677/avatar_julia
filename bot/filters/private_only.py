"""Обработка только личных чатов: группы и каналы игнорируются."""

from aiogram import F
from aiogram.enums import ChatType

PRIVATE_CHAT = F.chat.type == ChatType.PRIVATE

# CallbackQuery с сообщением из ЛС (инлайн-кнопка под сообщением бота)
CALLBACK_PRIVATE_CHAT = F.message.chat.type == ChatType.PRIVATE
