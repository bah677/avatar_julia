"""LLM: относится ли встреча к клубу + метаданные из конспекта в письме."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset(
    {"story", "educational", "webinar", "dialog", "manual_text", "testimonial"}
)


@dataclass(frozen=True)
class TelemostClassification:
    is_club_meeting: bool
    recommend_index: bool
    title: str
    meeting_topic: str
    content_type: str
    content_category: str
    product: str
    tags: str
    summary: str
    admin_note: str
    reason: str = ""

    def as_chroma_metadata(self, *, source_label: str) -> dict:
        return {
            "source": source_label[:80],
            "content_type": (self.content_type or "встреча")[:500],
            "content_category": self.content_category or "dialog",
            "product": (self.product or "general")[:500],
            "tags": (self.tags or "")[:500],
            "topic_title": (self.title or source_label)[:500],
            "voice_source": "expert",
            "import_source": "telemost_mail",
            "meeting_topic": (self.meeting_topic or "")[:500],
        }


_SYSTEM = """Ты помогаешь импортировать конспекты встреч из Яндекс.Телемоста в базу знаний эксперта.

На входе — тема письма и краткий конспект из тела письма (выжимка встречи).
Также дана инструкция куратора: какие встречи относятся к работе с клубом.

Верни ТОЛЬКО JSON:
{
  "is_club_meeting": true/false,
  "recommend_index": true/false,
  "title": "краткое название встречи",
  "meeting_topic": "о чём встреча",
  "content_type": "формат (встреча клуба, эфир, разбор, …)",
  "content_category": "dialog|webinar|educational|manual_text|…",
  "product": "продукт/клуб или пустая строка",
  "tags": "теги через запятую",
  "summary": "1–3 предложения сути",
  "admin_note": "одна короткая фраза для админа в Telegram: брать в RAG или нет",
  "reason": "почему так решил"
}

Правила:
- is_club_meeting — true только если встреча по инструкции куратора (работа с участниками клуба, «Разговоры с Богом» и т.п.).
- recommend_index — true если стоит грузить речь эксперта в RAG; false для посторонних созвонов, техники, личных встреч.
- Если сомневаешься — is_club_meeting=false, recommend_index=false, admin_note попроси решить вручную."""


def _parse_json(raw: str) -> Optional[TelemostClassification]:
    text = (raw or "").strip()
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    def _bool(v) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "да")

    cat = str(data.get("content_category") or "dialog").strip().lower()
    if cat not in _VALID_CATEGORIES:
        cat = "dialog"

    return TelemostClassification(
        is_club_meeting=_bool(data.get("is_club_meeting")),
        recommend_index=_bool(data.get("recommend_index")),
        title=str(data.get("title") or "").strip()[:500],
        meeting_topic=str(data.get("meeting_topic") or "").strip()[:500],
        content_type=str(data.get("content_type") or "").strip()[:500],
        content_category=cat,
        product=str(data.get("product") or "").strip()[:500],
        tags=str(data.get("tags") or "").strip()[:500],
        summary=str(data.get("summary") or "").strip()[:2000],
        admin_note=str(data.get("admin_note") or "").strip()[:500],
        reason=str(data.get("reason") or "").strip()[:500],
    )


async def classify_telemost_summary(
    *,
    subject: str,
    body_summary: str,
    club_hint: str,
    default_product: str = "",
) -> TelemostClassification:
    from config import config

    subj = (subject or "").strip()
    body = (body_summary or "").strip()[:6000]
    hint = (club_hint or "").strip()

    user = f"Тема письма: {subj}\n\nКонспект из письма:\n{body}"
    if hint:
        user = f"Инструкция куратора:\n{hint}\n\n{user}"
    if default_product:
        user += f"\n\nПродукт по умолчанию: {default_product}"

    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    if key and body:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            r = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=600,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            out = r.choices[0].message.content if r.choices else ""
            parsed = _parse_json(out or "")
            if parsed:
                if default_product and not parsed.product:
                    return TelemostClassification(
                        is_club_meeting=parsed.is_club_meeting,
                        recommend_index=parsed.recommend_index,
                        title=parsed.title,
                        meeting_topic=parsed.meeting_topic,
                        content_type=parsed.content_type,
                        content_category=parsed.content_category,
                        product=default_product,
                        tags=parsed.tags,
                        summary=parsed.summary,
                        admin_note=parsed.admin_note,
                        reason=parsed.reason,
                    )
                return parsed
        except Exception as e:
            logger.warning("classify_telemost_summary: %s", e)

    low = f"{subj} {body}".lower()
    club = any(
        k in low
        for k in ("разговор", "клуб", "участник", "бог")
    )
    return TelemostClassification(
        is_club_meeting=club,
        recommend_index=club,
        title=subj[:200] or "Встреча Телемост",
        meeting_topic="",
        content_type="встреча",
        content_category="dialog",
        product=default_product or "",
        tags="",
        summary=body[:500],
        admin_note="Проверьте вручную — LLM недоступен.",
        reason="fallback",
    )
