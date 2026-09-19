"""Команды бота (личка): старт, справка, админы, служебный RAG."""

from __future__ import annotations

import asyncio
import logging
import time
from html import escape as html_escape
from typing import Optional

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.filters.private_only import CALLBACK_PRIVATE_CHAT, PRIVATE_CHAT
from bot.features.base import FeatureManager
from config import config

logger = logging.getLogger(__name__)

# Уведомление супер-админу о /start от не-админа (BOT_ACCESS_ADMIN_ONLY): не чаще раз в N сек на user_id.
_PROMOTE_NOTIFY_LAST: dict[int, float] = {}
_PROMOTE_NOTIFY_DEBOUNCE_SEC = 1800.0

def build_start_welcome(bot_label: str) -> str:
    """Текст /start: активный продукт и как класть материалы."""
    from course.products import active_product
    from config import config as app_config

    name = html_escape((bot_label or "").strip()) or "аватар"
    product_name = "—"
    try:
        if getattr(app_config, "COURSE_ENABLED", False):
            product_name = html_escape(active_product().name)
    except Exception:
        product_name = html_escape(getattr(app_config, "ACTIVE_PRODUCT", "") or "—")
    root = html_escape(getattr(app_config, "COURSE_DISK_ROOT", "/Аватар") or "/Аватар")
    return (
        f"🤖 <b>Привет!</b>\n\n"
        f"Я — <b>{name}</b>, редакция эксперта: забираю материалы продукта, "
        "разбираю их в банк идей и готовлю черновики для соцсетей в вашем голосе.\n\n"
        f"<b>Активный продукт:</b> {product_name}\n\n"
        "<b>Как класть материалы</b>\n"
        f"• На Яндекс.Диск в <code>{root}/00 Эксперт/</code> и папку продукта "
        "(уроки, конспекты, слайды, практики, эфиры, посты).\n"
        "• Ссылки на видео (YouTube, Vimeo) — прямо в эту личку.\n\n"
        "<b>Как работать</b>\n"
        "• Напишите задачу — сразу черновик.\n"
        "• <code>/plan</code> — план недели · <code>/lessons</code> — уроки · "
        "<code>/sync</code> — синхронизация Диска.\n\n"
        "Подробности — <code>/help</code>."
    )


_HELP_TEXT = (
    "<b>Контент-аватар эксперта</b>\n\n"
    "Бот работает как редакция: сам забирает материалы активного продукта, "
    "один раз разбирает их в карточки идей и пишет черновики в вашем голосе.\n\n"
    "<b>Команды</b>\n"
    "<code>/start</code> — приветствие и активный продукт\n"
    "<code>/plan</code> — собрать план на неделю сейчас\n"
    "<code>/focus [текст|off]</code> — фокус контента\n"
    "<code>/new</code> — новая задача (сброс контекста)\n"
    "<code>/lessons</code> — уроки и состояние материалов\n"
    "<code>/lesson 2.4</code> — паспорт урока\n"
    "<code>/bank [урок|тип]</code> — карточки идей\n"
    "<code>/style</code> — паспорт голоса\n"
    "<code>/sync</code> — синхронизировать Яндекс.Диск\n"
    "<code>/queue</code> — очередь обработки\n"
    "<code>/help</code> — эта справка\n\n"
    "<b>Без команды</b>\n"
    "ссылка на видео → приём; текст или голос → задача; "
    "ответ на черновик → правка.\n\n"
    "<b>Для разработчика</b>\n"
    "<code>/admin_add</code> · <code>/admin_block</code> · "
    "<code>/costs</code> · <code>/remine</code> · "
    "<code>/summary</code> · <code>/rag_clear</code>"
)


class AppCommandHandlers:
    def __init__(self, dp: Dispatcher, feature_manager: FeatureManager):
        self.dp = dp
        self.features = feature_manager

    def register_handlers(self) -> None:
        self.dp.message.register(
            self._start_handler, PRIVATE_CHAT, Command(commands=["start"])
        )
        self.dp.message.register(
            self._admin_add_handler,
            PRIVATE_CHAT,
            Command(commands=["admin_add"]),
        )
        self.dp.message.register(
            self._admin_block_handler,
            PRIVATE_CHAT,
            Command(commands=["admin_block"]),
        )
        self.dp.message.register(
            self._rag_clear_handler,
            PRIVATE_CHAT,
            Command(commands=["rag_clear"]),
        )
        self.dp.callback_query.register(
            self._golden_like_handler,
            CALLBACK_PRIVATE_CHAT,
            F.data == "rag_vote:up",
        )
        self.dp.callback_query.register(
            self._creative_rag_callback,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith("crt:"),
        )
        self.dp.callback_query.register(
            self._rag_clear_confirm_callback,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith("rag_clear:"),
        )
        self.dp.callback_query.register(
            self._promote_admin_callback,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith("promote_admin:"),
        )
        logger.info(
            "✅ /start → меню; скрытые /admin_add /admin_block /rag_clear; 👍 golden; crt:*"
        )

    def _user_storage_from_messaging(self):
        messaging = self.features.get("messaging")
        return getattr(messaging, "user_storage", None)

    async def _start_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        messaging = self.features.get("messaging")
        stor = getattr(messaging, "user_storage", None)
        if stor is None:
            logger.error("/start: user_storage недоступен")
            await message.answer("Сервис временно недоступен. Попробуйте позже.")
            return

        args = (message.text or "").split()
        param = args[1] if len(args) > 1 else None

        existing_user = await stor.get_user(uid)
        is_new_user = existing_user is None

        ok = await stor.save_user_from_message(message)
        if not ok:
            logger.warning("/start: не удалось сохранить пользователя %s", uid)

        if param and param.startswith("ref_"):
            referrer_id_str = param[4:]
            try:
                ref = self.features.get_optional("referral")
                if ref:
                    await ref.register_referral(message, referrer_id_str, is_new_user)
            except Exception as e:
                logger.error("ref link: %s", e)

        if config.BOT_ACCESS_ADMIN_ONLY:
            is_super = bool(config.SUPER_ADMIN_ID and uid == config.SUPER_ADMIN_ID)
            is_adm = await stor.is_bot_admin(uid) if stor else False
            if not is_super and not is_adm:
                await state.clear()
                await message.answer(
                    "<b>Доступ по приглашению</b>\n\n"
                    "Бот работает только для администраторов из белого списка.\n"
                    "Заявка отправлена супер-администратору — ожидайте решения.\n\n"
                    "<i>Пока доступ не выдан, остальные команды недоступны.</i>",
                    parse_mode=ParseMode.HTML,
                )
                await self._maybe_notify_super_promote_request(message, stor)
                return

        await state.clear()
        menu = self.features.get_optional("main_menu")
        if menu:
            await menu.send_menu(message)
            return
        await message.answer("Откройте /menu")

    async def _maybe_notify_super_promote_request(self, message: Message, stor) -> None:
        sid = int(config.SUPER_ADMIN_ID or 0)
        if not sid:
            return
        uid = message.from_user.id if message.from_user else 0
        if uid == sid:
            return
        try:
            if await stor.is_bot_admin(uid):
                return
        except Exception:
            return

        now = time.monotonic()
        prev = _PROMOTE_NOTIFY_LAST.get(uid, 0.0)
        if now - prev < _PROMOTE_NOTIFY_DEBOUNCE_SEC:
            return
        _PROMOTE_NOTIFY_LAST[uid] = now

        u = message.from_user
        un = f"@{u.username}" if u and u.username else "—"
        fn = (u.first_name or "").strip() if u else ""
        ln = (u.last_name or "").strip() if u else ""
        nm = (fn + (" " + ln if ln else "")).strip() or "—"
        text = (
            "<b>Запуск бота</b> (ожидает доступ)\n\n"
            f"ID: <code>{uid}</code>\n"
            f"Имя: {html_escape(nm)}\n"
            f"Username: {html_escape(un)}\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Сделать админом",
                        callback_data=f"promote_admin:{uid}",
                    )
                ]
            ]
        )
        try:
            await message.bot.send_message(
                sid,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("super promote notify: %s", e)

    async def _promote_admin_callback(self, callback: CallbackQuery):
        sid = int(config.SUPER_ADMIN_ID or 0)
        uid = callback.from_user.id if callback.from_user else 0
        if not sid or uid != sid:
            await callback.answer("Только суперадмин.", show_alert=True)
            return
        data = (callback.data or "").strip()
        try:
            target = int(data.split(":", 1)[1], 10)
        except (IndexError, ValueError):
            await callback.answer("Некорректные данные", show_alert=True)
            return
        stor = self._user_storage_from_messaging()
        if stor is None:
            await callback.answer("Нет БД", show_alert=True)
            return
        ok = await stor.add_bot_admin(target, added_by=sid)
        if not ok:
            await callback.answer("Не удалось записать в БД", show_alert=True)
            return
        try:
            if callback.message:
                await callback.message.edit_text(
                    f"✅ Пользователь <code>{target}</code> добавлен в админы бота.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
        except Exception:
            pass
        await callback.answer("Готово")
        try:
            from bot.content.admin_onboarding import ADMIN_ONBOARDING_HTML

            await callback.bot.send_message(
                target,
                ADMIN_ONBOARDING_HTML,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning("admin onboarding DM to %s: %s", target, e)

    async def _help_handler(self, message: Message, state: FSMContext):
        await message.answer(_HELP_TEXT, parse_mode=ParseMode.HTML)

    async def _creative_new_command(self, message: Message, state: FSMContext):
        messaging = self.features.get_optional("messaging")
        if not messaging or not getattr(messaging, "creative_coord", None):
            await message.answer("Сервис временно недоступен.")
            return
        await messaging.creative_coord.on_command_new(message)

    async def _rag_summary_handler(self, message: Message, state: FSMContext):
        """Сводка по expert_materials: чанки по content_type, content_category, product."""
        from rag.expert_stats import compute_expert_materials_statistics, format_expert_stats_html

        messaging = self.features.get_optional("messaging")
        app = getattr(messaging, "bot", None) if messaging else None
        rs = getattr(app, "rag_stack", None) if app else None
        if rs is None:
            await message.answer(
                "База RAG не поднята (<code>RAG_ENABLED</code> выключен или нет ключа OpenAI).",
                parse_mode=ParseMode.HTML,
            )
            return

        try:
            stats = await asyncio.to_thread(
                compute_expert_materials_statistics, rs.vectors
            )
        except Exception as e:
            logger.exception("rag summary failed: %s", e)
            await message.answer(
                f"Не удалось прочитать Chroma: <code>{html_escape(str(e))}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        golden_n = -1
        try:
            golden_n = int(rs.vectors.golden_collection.count())
        except Exception:
            pass

        text = format_expert_stats_html(stats, golden_count=golden_n)
        if len(text) > 4000:
            text = text[:3900] + "\n\n<i>… обрезано по лимиту Telegram.</i>"
        await message.answer(text, parse_mode=ParseMode.HTML)

    async def _rag_topics_export_handler(self, message: Message, state: FSMContext):
        """TSV: group_chat_id, thread_id, topic_name, content_type, product, content_category."""
        if not self._super_admin_only(message):
            await message.answer(
                "Команда доступна только суперадмину.",
                parse_mode=ParseMode.HTML,
            )
            return
        groups_map = config.rag_groups_map
        if not groups_map:
            await message.answer(
                "RAG-группы не заданы (ни <code>RAG_GROUPS</code>, ни <code>RAG_GROUP_CHAT_ID</code>).",
                parse_mode=ParseMode.HTML,
            )
            return
        stor = self._user_storage_from_messaging()
        if stor is None:
            await message.answer("Сервис временно недоступен.")
            return
        from bot.features.rag_group_metadata import resolve_content_type_product_category

        header = (
            "group_chat_id\tthread_id\ttopic_name\tcontent_type\tproduct\tcontent_category"
        )
        lines = [header]
        for gid in sorted(groups_map.keys()):
            try:
                snap = await stor.forum_topic_names_snapshot_for_chat(gid)
            except Exception as e:
                logger.exception("rag_topics (group %s): %s", gid, e)
                continue
            for (gc, tid), name in sorted(snap.items(), key=lambda x: (x[0][0], x[0][1])):
                ct, pr, cat = resolve_content_type_product_category(name)
                safe_name = (name or "").replace("\t", " ").replace("\n", " ").replace("\r", "")
                lines.append(f"{gc}\t{tid}\t{safe_name}\t{ct}\t{pr}\t{cat}")

        gids_str = ", ".join(str(g) for g in sorted(groups_map.keys()))
        body = ("\n".join(lines) + "\n").encode("utf-8")
        cap = (
            f"Топики из <code>forum_topic_names</code> для RAG-групп: <code>{gids_str}</code> "
            f"(строк: {len(lines) - 1}). Колонки как при разборе для Chroma."
        )
        await message.answer_document(
            BufferedInputFile(body, filename="rag_topics.tsv"),
            caption=cap,
            parse_mode=ParseMode.HTML,
        )

    async def _creative_rag_callback(self, callback: CallbackQuery):
        messaging = self.features.get_optional("messaging")
        if not messaging or not getattr(messaging, "creative_coord", None):
            await callback.answer("Недоступно", show_alert=True)
            return
        await messaging.creative_coord.on_callback(callback)

    async def _support_handler(self, message: Message, state: FSMContext):
        support = self.features.get("support")
        await support.start_support(message, state)

    async def _payment_handler(self, message: Message, state: FSMContext):
        pay = self.features.get("payment")
        await pay.show_donation_menu(message)

    async def _affiliate_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        ref = self.features.get("referral")
        await ref.show_affiliate_link(message, uid)

    async def _golden_like_handler(self, callback: CallbackQuery):
        messaging = self.features.get("messaging")
        if messaging is None or not hasattr(messaging, "on_rag_vote_feedback"):
            await callback.answer("Сервис недоступен", show_alert=True)
            return
        await messaging.on_rag_vote_feedback(callback)

    def _super_admin_only(self, message: Message) -> bool:
        uid = message.from_user.id if message.from_user else 0
        sid = config.SUPER_ADMIN_ID
        if not sid:
            return False
        return uid == sid

    @staticmethod
    def _parse_command_target_user_id(message: Message) -> Optional[int]:
        text = (message.text or "").strip()
        parts = text.split()
        if len(parts) < 2:
            return None
        raw = parts[1].strip()
        try:
            return int(raw, 10)
        except ValueError:
            return None

    async def _admin_add_handler(self, message: Message, state: FSMContext):
        if not self._super_admin_only(message):
            await message.answer(
                "Команда доступна только суперадмину.",
                parse_mode=ParseMode.HTML,
            )
            return
        stor = self._user_storage_from_messaging()
        if stor is None:
            await message.answer("Сервис временно недоступен.")
            return
        tid = self._parse_command_target_user_id(message)
        if tid is None:
            await message.answer(
                "Укажите числовой Telegram ID пользователя:\n"
                "<code>/admin_add 123456789</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if tid == config.SUPER_ADMIN_ID:
            await message.answer("Этот ID уже суперадмин в .env, запись в таблице не нужна.")
            return
        uid = message.from_user.id if message.from_user else 0
        ok = await stor.add_bot_admin(tid, added_by=uid)
        if ok:
            await message.answer(
                f"✅ Пользователь <code>{tid}</code> добавлен в администраторы бота.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer("❌ Не удалось сохранить. Проверьте БД и логи.")

    async def _admin_block_handler(self, message: Message, state: FSMContext):
        if not self._super_admin_only(message):
            await message.answer(
                "Команда доступна только суперадмину.",
                parse_mode=ParseMode.HTML,
            )
            return
        stor = self._user_storage_from_messaging()
        if stor is None:
            await message.answer("Сервис временно недоступен.")
            return
        tid = self._parse_command_target_user_id(message)
        if tid is None:
            await message.answer(
                "Укажите числовой Telegram ID:\n"
                "<code>/admin_block 123456789</code>\n"
                "(удаляет из списка админов в таблице)",
                parse_mode=ParseMode.HTML,
            )
            return
        if tid == config.SUPER_ADMIN_ID:
            await message.answer(
                "Суперадмин задан в .env; из таблицы не удаляется. "
                "Уберите SUPER_ADMIN_ID в конфиге при необходимости.",
                parse_mode=ParseMode.HTML,
            )
            return
        ok = await stor.remove_bot_admin(tid)
        if ok:
            await message.answer(
                f"Готово. Пользователь <code>{tid}</code> снят с роли админа в боте (таблица).",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer("❌ Ошибка при обновлении БД.")

    async def _rag_clear_handler(self, message: Message, state: FSMContext):
        """Запрос подтверждения перед полной очисткой Chroma. Только суперадмин."""
        if not self._super_admin_only(message):
            await message.answer(
                "Команда доступна только суперадмину.",
                parse_mode=ParseMode.HTML,
            )
            return
        messaging = self.features.get_optional("messaging")
        app = getattr(messaging, "bot", None) if messaging else None
        rs = getattr(app, "rag_stack", None) if app else None
        if rs is None:
            await message.answer(
                "RAG не поднят (<code>RAG_ENABLED</code> выключен или нет ключа OpenAI). "
                "Очистка недоступна.",
                parse_mode=ParseMode.HTML,
            )
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Очистить RAG",
                        callback_data="rag_clear:yes",
                    ),
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data="rag_clear:no",
                    ),
                ]
            ]
        )
        await message.answer(
            "⚠️ <b>Очистка RAG</b>\n\n"
            "Будут удалены <b>все</b> векторные данные в Chroma: чанки материалов из группы "
            "и записи «золотого фонда» (👍).\n\n"
            "<b>Вернуть содержимое нельзя</b> — базу можно восстановить только заново "
            "(новые сообщения в RAG-группе и новые отметки 👍).\n\n"
            "Подтвердите действие:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    async def _rag_clear_confirm_callback(self, callback: CallbackQuery):
        uid = callback.from_user.id if callback.from_user else 0
        if not config.SUPER_ADMIN_ID or uid != config.SUPER_ADMIN_ID:
            await callback.answer("Недоступно", show_alert=True)
            return

        action = (callback.data or "").split(":", 1)[-1].strip()
        msg = callback.message

        async def _strip_keyboard() -> None:
            if msg:
                try:
                    await msg.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass

        if action == "no":
            await _strip_keyboard()
            if msg:
                try:
                    await msg.edit_text(
                        "Очистка RAG отменена.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    await callback.message.answer(
                        "Очистка RAG отменена.", parse_mode=ParseMode.HTML
                    )
            await callback.answer()
            return

        if action != "yes":
            await callback.answer()
            return

        messaging = self.features.get_optional("messaging")
        app = getattr(messaging, "bot", None) if messaging else None
        rs = getattr(app, "rag_stack", None) if app else None
        if rs is None:
            await _strip_keyboard()
            if msg:
                try:
                    await msg.edit_text(
                        "RAG уже не доступен — очистка не выполнена.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    await callback.message.answer(
                        "RAG уже не доступен — очистка не выполнена.",
                        parse_mode=ParseMode.HTML,
                    )
            await callback.answer("RAG недоступен", show_alert=True)
            return

        await callback.answer("Выполняю очистку…")

        try:
            ne, ng = await asyncio.to_thread(rs.reset_all_vector_data)
        except Exception as e:
            logger.exception("rag_clear failed: %s", e)
            if msg:
                try:
                    await msg.edit_text(
                        f"❌ Ошибка Chroma: <code>{html_escape(str(e))}</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    )
                except Exception:
                    await callback.message.answer(
                        f"❌ Ошибка Chroma: <code>{html_escape(str(e))}</code>",
                        parse_mode=ParseMode.HTML,
                    )
            return

        logger.warning(
            "RAG Chroma cleared by super_admin user_id=%s (expert=%s golden=%s)",
            uid,
            ne,
            ng,
        )
        done = (
            "✅ <b>RAG очищен</b>\n\n"
            "Коллекции Chroma пересозданы пустыми.\n"
            f"Чанков материалов: <code>{ne}</code>, golden: <code>{ng}</code>."
        )
        if msg:
            try:
                await msg.edit_text(done, parse_mode=ParseMode.HTML, reply_markup=None)
            except Exception:
                await callback.message.answer(done, parse_mode=ParseMode.HTML)
