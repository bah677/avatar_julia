"""Ручной запуск: python -m yandex_disk.cli_sync"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO)


async def _main() -> int:
    from config import config
    from openai_client.assistant import OpenAIClient
    from storage.user_storage import UserStorage
    from bot.integrations.rag_bridge import try_build_rag_stack
    from yandex_disk.sync import YandexDiskSyncService

    storage = UserStorage(config.database_url)
    await storage.connect()
    try:
        rag = try_build_rag_stack(config)
        oai = OpenAIClient(storage)
        svc = YandexDiskSyncService.from_config(
            config,
            user_storage=storage,
            openai_client=oai,
            material_index=rag.materials if rag else None,
        )
        if not svc.enabled:
            print("Yandex Disk sync not configured (login, RAG, sources)")
            return 1
        results = await svc.sync_all()
        for r in results:
            print(
                f"{r.source_id}: scanned={r.scanned} matched={r.matched} "
                f"indexed={r.indexed} skipped={r.skipped} errors={r.errors}"
            )
        return 0
    finally:
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
