"""Письмо «Запись встречи» — ссылка на видео (без TXT)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from telemost_mail.email_parse import _RE_MEETING_ID, parse_telemost_email

_RE_RECORDING_SUBJ = re.compile(
    r"запись\s+встречи",
    re.IGNORECASE,
)
_RE_VIDEO_URL = re.compile(
    r"https?://(?:disk\.yandex\.(?:ru|com)|yadi\.sk|"
    r"telemost\.yandex\.ru|cloud\.yandex\.ru|"
    r"downloader\.disk\.yandex\.ru)[^\s<>\"')\]]+",
    re.IGNORECASE,
)
_RE_ANY_MP4 = re.compile(r"https?://[^\s<>\"')\]]+\.mp4(?:\?[^\s<>\"')\]]*)?", re.I)


@dataclass(frozen=True)
class RecordingMailParsed:
    meeting_id: str
    meeting_date: str
    video_url: str
    audio_url: str
    subject: str


def extract_video_urls(text: str) -> List[str]:
    blob = (text or "").strip()
    if not blob:
        return []
    found: List[str] = []
    for pat in (_RE_VIDEO_URL, _RE_ANY_MP4):
        for m in pat.finditer(blob):
            u = m.group(0).rstrip(").,;]")
            if u not in found:
                found.append(u)
    return found


def is_recording_subject(subject: str) -> bool:
    return bool(_RE_RECORDING_SUBJ.search(subject or ""))


_RE_VIDEO_LINE = re.compile(
    r"ссылка\s+на\s+видео\s*:\s*(https?://\S+)",
    re.IGNORECASE,
)
_RE_MEETING_PAGE = re.compile(r"telemost\.yandex\.ru/j/", re.IGNORECASE)


def _url_video_score(url: str) -> int:
    u = (url or "").lower()
    if "yadi.sk" in u or "disk.yandex" in u or "downloader.disk.yandex" in u:
        return 100
    if u.endswith(".mp4") or ".mp4?" in u:
        return 90
    if _RE_MEETING_PAGE.search(u):
        return 0
    if "telemost.yandex.ru" in u:
        return 10
    return 50


def pick_best_video_url(text: str) -> Optional[str]:
    """Предпочитает «Ссылка на видео» и yadi.sk, не страницу встречи telemost/j/."""
    blob = (text or "").strip()
    if not blob:
        return None

    explicit: List[str] = []
    for m in _RE_VIDEO_LINE.finditer(blob):
        u = m.group(1).rstrip(").,;]")
        if u:
            explicit.append(u)
    if explicit:
        return max(explicit, key=_url_video_score)

    urls = extract_video_urls(blob)
    if not urls:
        return None
    ranked = sorted(urls, key=_url_video_score, reverse=True)
    best = ranked[0]
    if _url_video_score(best) <= 0 and len(ranked) > 1:
        return ranked[1]
    return best if _url_video_score(best) > 0 else None


_RE_AUDIO_LINE = re.compile(
    r"ссылка\s+на\s+аудио\s*:\s*(https?://\S+)",
    re.IGNORECASE,
)


def pick_best_audio_url(text: str) -> Optional[str]:
    blob = (text or "").strip()
    if not blob:
        return None
    for m in _RE_AUDIO_LINE.finditer(blob):
        u = m.group(1).rstrip(").,;]")
        if u and _url_video_score(u) > 0:
            return u
    return None


def parse_recording_email(subject: str, body: str) -> Optional[RecordingMailParsed]:
    """None если это не письмо с записью или нет ссылки на видео/аудио."""
    subj = (subject or "").strip()
    b = (body or "").strip()
    video_url = pick_best_video_url(b) or ""
    audio_url = pick_best_audio_url(b) or ""
    if not is_recording_subject(subj) and not video_url and not audio_url:
        return None
    if not video_url and not audio_url:
        return None

    base = parse_telemost_email(subj, b)
    meeting_id = base.meeting_id
    if not meeting_id:
        m = _RE_MEETING_ID.search(subj) or _RE_MEETING_ID.search(b)
        if m:
            meeting_id = m.group(1)

    return RecordingMailParsed(
        meeting_id=(meeting_id or "").strip(),
        meeting_date=base.meeting_date,
        video_url=video_url,
        audio_url=audio_url,
        subject=subj,
    )
