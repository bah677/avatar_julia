"""Uvicorn внутри процесса бота: один процесс, одна база, один Chroma."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WebStudioServer:
    def __init__(self, bot_app: Any):
        self._bot_app = bot_app
        self._task: Optional[asyncio.Task] = None
        self._server: Any = None

    @staticmethod
    def should_start() -> bool:
        from config import config

        if not getattr(config, "WEB_ENABLED", False):
            return False
        if not getattr(config, "COURSE_ENABLED", False):
            logger.warning("WEB_ENABLED=1, но COURSE_ENABLED=0 — студия не запущена")
            return False
        secret = str(getattr(config, "WEB_SECRET", "") or "").strip() or str(
            getattr(config, "WEB_AUTH_TOKEN", "") or ""
        ).strip()
        if not secret:
            logger.warning("WEB_ENABLED=1, но WEB_SECRET пуст — студия не запущена")
            return False
        return True

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self.should_start():
            return
        from config import config

        try:
            import uvicorn
        except ImportError:
            logger.error("Веб-студия: нет uvicorn (pip install -r requirements.txt)")
            return

        from web.api import create_app

        host = str(getattr(config, "WEB_HOST", "127.0.0.1") or "127.0.0.1")
        port = int(getattr(config, "WEB_PORT", 8800) or 8800)
        cfg = uvicorn.Config(
            create_app(self._bot_app),
            host=host,
            port=port,
            log_level="warning",
            lifespan="off",
            access_log=False,
        )
        self._server = uvicorn.Server(cfg)
        # uvicorn по умолчанию ставит свои обработчики сигналов и уводит остановку
        # процесса из-под aiogram — боту они не нужны.
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._server.serve(), name="web_studio")
        domain = str(getattr(config, "WEB_DOMAIN", "") or "").strip()
        logger.warning(
            "Веб-студия: %s (слушает %s:%s)",
            f"https://{domain}" if domain else f"http://{host}:{port}",
            host,
            port,
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("Веб-студия: остановка с ошибкой: %s", e)
        self._task = None
        self._server = None
