"""Разбор тела письма Телемоста (keeper@telemost.yandex.ru)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TelemostEmailParsed:
    meeting_id: str
    meeting_date: str
    meeting_url: str
    started_at: str
    summary_text: str

    def context_for_llm(self) -> str:
        parts = []
        if self.meeting_date:
            parts.append(f"Дата встречи: {self.meeting_date}")
        if self.meeting_id:
            parts.append(f"Номер встречи: {self.meeting_id}")
        if self.started_at:
            parts.append(f"Начало: {self.started_at}")
        if self.meeting_url:
            parts.append(f"Ссылка: {self.meeting_url}")
        if self.summary_text:
            parts.append(f"\nКонспект:\n{self.summary_text}")
        return "\n".join(parts).strip()


_RE_MEETING_ID = re.compile(r"№\s*(\d+)")
_RE_DATE_SUBJECT = re.compile(
    r"конспект\s+встречи\s+от\s+(\d{2}\.\d{2}\.\d{4})",
    re.IGNORECASE,
)
_RE_DATE_BODY = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
_RE_STARTED = re.compile(
    r"конспектирование\s+началось\s+(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_RE_URL = re.compile(r"https://telemost\.yandex\.ru/\S+", re.IGNORECASE)


def _extract_summary(body: str) -> str:
    """Темы, задачи и выводы — без шапки письма."""
    text = (body or "").strip()
    if not text:
        return ""

    cut_markers = (
        "Задачи",
        "Тема 1.",
        "Тема 1 ",
        "Тема 2.",
    )
    start = len(text)
    for m in cut_markers:
        idx = text.find(m)
        if idx != -1:
            start = min(start, idx)
    if start < len(text):
        return text[start:].strip()

    skip_prefixes = (
        "Конспект встречи",
        "№",
        "Конспектирование началось",
        "Ссылка на встречу",
        "Во вложении",
        "В конспекте могут быть неточности",
    )
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(s.startswith(p) for p in skip_prefixes):
            continue
        if _RE_URL.search(s):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def parse_telemost_email(subject: str, body: str) -> TelemostEmailParsed:
    subj = (subject or "").strip()
    b = (body or "").strip()

    meeting_id = ""
    m = _RE_MEETING_ID.search(b)
    if m:
        meeting_id = m.group(1)

    meeting_date = ""
    ms = _RE_DATE_SUBJECT.search(subj)
    if ms:
        meeting_date = ms.group(1)
    elif not meeting_date:
        mb = _RE_DATE_BODY.search(b)
        if mb:
            meeting_date = mb.group(1)

    started_at = ""
    st = _RE_STARTED.search(b)
    if st:
        started_at = st.group(1).strip()

    meeting_url = ""
    mu = _RE_URL.search(b)
    if mu:
        meeting_url = mu.group(0).rstrip(").,;")

    summary_text = _extract_summary(b)

    return TelemostEmailParsed(
        meeting_id=meeting_id,
        meeting_date=meeting_date,
        meeting_url=meeting_url,
        started_at=started_at,
        summary_text=summary_text,
    )


def is_telemost_sender(sender: str, markers: tuple[str, ...]) -> bool:
    s = (sender or "").lower()
    if "keeper@telemost.yandex.ru" in s:
        return True
    return any(m.lower() in s for m in markers if m)
