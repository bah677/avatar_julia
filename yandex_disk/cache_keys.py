"""Ключи кэша импорта для файлов Я.Диска."""

from __future__ import annotations


def yandex_disk_cache_key(source_id: str, remote_path: str) -> str:
    return f"{(source_id or '').strip()}:{(remote_path or '').strip()}"
