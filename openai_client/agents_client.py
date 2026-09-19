"""
Клиент DeepSeek API с историей диалога из БД (messages).

Системный промпт по умолчанию — контент-коуч с блоками RAG (``content_coach_prompt``).
Опционально ``system_prompt_override`` для тестов или особой сборки.
"""

import asyncio
import html as html_module
import logging
import os
import re
import uuid
from typing import Any, List, Optional, Sequence

from openai import AsyncOpenAI

from openai_client.content_coach_prompt import build_content_coach_system_prompt
from bot.utils.telegram_html import strip_llm_code_fence
from storage.db.llm_token_normalize import extract_token_counts_and_extras

logger = logging.getLogger(__name__)

# Таймауты DeepSeek: HTTP-клиент не должен быть короче asyncio.wait_for на запрос.
_DEEPSEEK_HTTP_TIMEOUT_SEC = 150.0
_DEEPSEEK_CHAT_WAIT_SEC = 120.0
_DEEPSEEK_HTML_FORMAT_WAIT_SEC = 120.0

# token_usage / аналитика: дополнительный вызов только для разметки → HTML (DeepSeek).
TELEGRAM_HTML_FORMAT_REQUEST_KIND = "telegram_html_format_auxiliary"

_TELEGRAM_HTML_FORMAT_SYSTEM = (
    "Ты приводишь текст к разметке Telegram Bot API (parse_mode HTML).\n"
    "На входе — ответ аватара, иногда с Markdown (** ## ``` и т.п.), "
    "и краткий контекст диалога (если дан — для согласованности, не дублируй его в выводе).\n"
    "На выходе верни ОДИН блок текста: только теги "
    "<b>, <strong>, <i>, <em>, <u>, <s>, <code>, <pre>, "
    "<a href=\"https://…\">, <blockquote>. "
    "Длинные цитаты при необходимости оборачивай в <blockquote>…</blockquote>.\n"
    "Не используй Markdown-символы. Не добавляй пояснений, только готовый HTML-текст."
)


def _strip_for_history_match(s: str) -> str:
    """Сравнение текста из БД (может быть HTML) с текстом из aiogram.message.text."""
    if not s:
        return ""
    t = re.sub(r"<[^>]+>", " ", s)
    t = html_module.unescape(t)
    return " ".join(t.split())


def _format_context_tail(history: List[dict], *, max_turns: int = 8, max_chars_per: int = 1200) -> str:
    lines = []
    for msg in history[-max_turns:]:
        role = msg.get("role") or ""
        content = (msg.get("content") or "")[:max_chars_per]
        if not content.strip():
            continue
        label = "Пользователь" if role == "user" else "Аватар"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


class AgentsClient:
    CHAT_MODEL = "deepseek-v4-flash"

    @property
    def chat_model(self) -> str:
        from config import config

        return (getattr(config, "CONTENT_WRITER_MODEL", "") or self.CHAT_MODEL).strip() or self.CHAT_MODEL

    def __init__(self, user_storage, *, system_prompt_override: Optional[str] = None):
        self.user_storage = user_storage

        self.client = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            timeout=_DEEPSEEK_HTTP_TIMEOUT_SEC,
            max_retries=2,
        )

        self._system_prompt_override = system_prompt_override
        if system_prompt_override is not None:
            logger.info(
                "✅ AgentsClient: статический system prompt (override), %s символов",
                len(system_prompt_override),
            )
        else:
            logger.info(
                "✅ AgentsClient: динамический промпт контент-коуча (RAG-блоки в каждом запросе)"
            )

    # Сколько последних сообщений из DM подаём агенту в контекст.
    HISTORY_LIMIT = 20

    async def run(
        self,
        user_message: str,
        user_id: int,
        *,
        retrieved_context: str = "",
        golden_block: str = "",
    ) -> Optional[str]:
        """
        Запрос к DeepSeek с историей ЛС. Без override системный промпт собирается из RAG + golden.
        """
        try:
            history = await self.user_storage.get_private_chat_history(
                user_id, limit=self.HISTORY_LIMIT
            )

            if self._system_prompt_override is not None:
                system = self._system_prompt_override
            else:
                system = build_content_coach_system_prompt(
                    retrieved_context=retrieved_context,
                    golden_block=golden_block,
                )

            messages = [{"role": "system", "content": system}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})

            if (
                not history
                or history[-1]["role"] != "user"
                or _strip_for_history_match(history[-1]["content"])
                != _strip_for_history_match(user_message)
            ):
                messages.append({"role": "user", "content": user_message})

            dup = bool(
                history
                and history[-1]["role"] == "user"
                and _strip_for_history_match(history[-1]["content"])
                == _strip_for_history_match(user_message)
            )
            rag_note = ""
            if self._system_prompt_override is None:
                rag_note = f" rag_ctx={len(retrieved_context or '')} golden={len(golden_block or '')}"
            logger.info(
                "📨 DeepSeek request for user %s: %s messages (history_rows=%s, dup_user_skipped=%s)%s",
                user_id,
                len(messages),
                len(history),
                dup,
                rag_note,
            )

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.chat_model,
                    messages=messages,
                    temperature=0.72,
                    max_tokens=3072,
                ),
                timeout=_DEEPSEEK_CHAT_WAIT_SEC,
            )

            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            reply_text: Optional[str] = None
            if response.choices and response.choices[0].message:
                reply_text = response.choices[0].message.content

            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.chat_model,
                usage=usage,
                request_kind="chat_completion",
                request_id=request_id,
            )
            pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type=f"deepseek_{self.chat_model}_chat_completion",
                data={
                    "provider": "deepseek",
                    "request_id": request_id,
                    "model": self.chat_model,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                },
                source="deepseek",
                outcome="success",
            )

            return reply_text

        except asyncio.TimeoutError:
            logger.error("❌ DeepSeek timeout for user %s", user_id)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_timeout",
                data={"model": self.chat_model},
                source="deepseek",
                outcome="error",
            )
            return None
        except Exception as e:
            logger.error("❌ DeepSeek API error for user %s: %s", user_id, e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_api_error",
                data={"model": self.chat_model, "error": str(e)},
                source="deepseek",
                outcome="error",
            )
            return None

    async def run_with_messages(
        self,
        messages: Sequence[dict[str, Any]],
        user_id: int,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        log_event_type: str = "deepseek_task_messages",
    ) -> Optional[str]:
        """
        Запрос к DeepSeek с готовым списком messages (системный промпт + история + пользователь).
        Не подмешивает историю ЛС из БД.
        """
        seq = [dict(m) for m in messages]
        if not seq:
            return None
        try:
            logger.info(
                "📨 DeepSeek run_with_messages user=%s turns=%s max_tokens=%s",
                user_id,
                len(seq),
                max_tokens,
            )
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.chat_model,
                    messages=seq,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=_DEEPSEEK_CHAT_WAIT_SEC,
            )
            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            reply_text: Optional[str] = None
            if response.choices and response.choices[0].message:
                reply_text = response.choices[0].message.content

            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.chat_model,
                usage=usage,
                request_kind=log_event_type,
                request_id=request_id,
            )
            pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type=f"deepseek_{self.chat_model}_{log_event_type}",
                data={
                    "provider": "deepseek",
                    "request_id": request_id,
                    "model": self.chat_model,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                    "turn_count": len(seq),
                },
                source="deepseek",
                outcome="success",
            )
            return reply_text
        except asyncio.TimeoutError:
            logger.error("❌ DeepSeek timeout (run_with_messages) user %s", user_id)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_timeout",
                data={"model": self.chat_model, "mode": "run_with_messages"},
                source="deepseek",
                outcome="error",
            )
            return None
        except Exception as e:
            logger.error("❌ DeepSeek run_with_messages error user %s: %s", user_id, e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_api_error",
                data={"model": self.chat_model, "error": str(e), "mode": "run_with_messages"},
                source="deepseek",
                outcome="error",
            )
            return None

    async def format_reply_to_telegram_html(self, raw_text: str, user_id: int) -> Optional[str]:
        """
        Отдельный вызов DeepSeek: Markdown/plain → HTML Telegram.
        В контекст подмешиваются последние реплики из того же источника, что и основной диалог.
        ``request_kind=telegram_html_format_auxiliary`` в token_usage.
        """
        try:
            hist = await self.user_storage.get_private_chat_history(
                user_id, limit=self.HISTORY_LIMIT
            )
            ctx = _format_context_tail(hist)
            if ctx.strip():
                user_block = (
                    "Контекст диалога (для согласованности; не копируй его в ответ, только разметь текст ниже):\n"
                    f"{ctx}\n\n---\n\n"
                    "Отформатируй в Telegram HTML только следующий ответ ассистента:\n\n"
                    f"{raw_text}"
                )
            else:
                user_block = (
                    "Отформатируй в Telegram HTML следующий текст (ответ аватара):\n\n"
                    f"{raw_text}"
                )

            messages = [
                {"role": "system", "content": _TELEGRAM_HTML_FORMAT_SYSTEM},
                {"role": "user", "content": user_block},
            ]
            est_out = min(8192, max(512, len(user_block) // 3 + len(raw_text) // 2 + 1024))

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.chat_model,
                    messages=messages,
                    temperature=0.15,
                    max_tokens=est_out,
                ),
                timeout=_DEEPSEEK_HTML_FORMAT_WAIT_SEC,
            )

            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            out = (
                response.choices[0].message.content.strip()
                if response.choices
                and response.choices[0].message
                and response.choices[0].message.content
                else ""
            )
            out = strip_llm_code_fence(out)

            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.chat_model,
                usage=usage,
                request_kind=TELEGRAM_HTML_FORMAT_REQUEST_KIND,
                request_id=request_id,
                metadata={
                    "purpose": "telegram_html_auxiliary_format",
                    "is_auxiliary_llm_formatting": True,
                    "formatter": "deepseek",
                    "input_chars": len(raw_text),
                    "output_chars": len(out),
                },
            )
            pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type=f"deepseek_{TELEGRAM_HTML_FORMAT_REQUEST_KIND}",
                data={
                    "provider": "deepseek",
                    "request_id": request_id,
                    "model": self.chat_model,
                    "request_kind": TELEGRAM_HTML_FORMAT_REQUEST_KIND,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                },
                source="deepseek",
                outcome="success",
            )
            return out or None

        except asyncio.TimeoutError:
            logger.error("❌ DeepSeek HTML format timeout for user %s", user_id)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_telegram_html_format_timeout",
                data={"model": self.chat_model},
                source="deepseek",
                outcome="error",
            )
            return None
        except Exception as e:
            logger.error("❌ DeepSeek HTML format error for user %s: %s", user_id, e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_telegram_html_format_error",
                data={"model": self.chat_model, "error": str(e)},
                source="deepseek",
                outcome="error",
            )
            return None
