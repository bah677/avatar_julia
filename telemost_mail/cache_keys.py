"""Ключи кэша импорта для писем Телемост."""

from __future__ import annotations


def telemost_mail_cache_key(*, imap_uid: str, message_id: str = "") -> str:
    mid = (message_id or "").strip()
    if mid:
        return f"mid:{mid}"
    return f"uid:{(imap_uid or '').strip()}"
