#!/usr/bin/env python3
"""
Точка входа автономного проекта «БиблияБот» (каталог = корень процесса).

Переменные: validate_biblia_bot_startup / load_biblia_bot_config в config.py —
BIBLIA_BOT_TOKEN, BIBLIA_DB_NAME, Postgres (DB_*), OPENAI_API_KEY, DEEPSEEK_API_KEY.
"""

import asyncio
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from bot_app import BotApplication
from config import load_biblia_bot_config, validate_biblia_bot_startup
from course.products import validate_course_startup

_PROJECT_ROOT = Path(__file__).resolve().parent
_LOG_DIR = _PROJECT_ROOT / "log"
_ARC_DIR = _LOG_DIR / "arc"
_BOT_LOG = _LOG_DIR / "bot.log"
_ERR_LOG = _LOG_DIR / "err.log"

_LOG_ROTATE_LINES = 10_000


def _rotate_logs() -> None:
    """Ротация логов при запуске бота.

    bot.log — если > 10 000 строк, переименовать в arc/bot-YYYY-MM-DD-N.log
    err.log — всегда переименовать в arc/err-YYYY-MM-DD-N.log
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _ARC_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")

    def _archive(src: Path, prefix: str) -> None:
        if not src.exists() or src.stat().st_size == 0:
            return
        n = 1
        while True:
            dest = _ARC_DIR / f"{prefix}-{today}-{n}.log"
            if not dest.exists():
                break
            n += 1
        shutil.move(str(src), str(dest))

    if _BOT_LOG.exists():
        try:
            line_count = sum(1 for _ in open(_BOT_LOG, "rb"))
        except OSError:
            line_count = 0
        if line_count > _LOG_ROTATE_LINES:
            _archive(_BOT_LOG, "bot")

    _archive(_ERR_LOG, "err")


def _setup_logging() -> None:
    bc = load_biblia_bot_config()
    log_lvl_name = (os.getenv("LOG_LEVEL") or str(bc.LOG_LEVEL) or "INFO").strip().upper()
    rag_debug = (os.getenv("RAG_INDEXER_DEBUG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ) or log_lvl_name == "DEBUG"
    lvl = getattr(logging, str(bc.LOG_LEVEL).upper(), logging.INFO)
    if rag_debug:
        lvl = logging.DEBUG

    _rotate_logs()

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    bot_file_handler = logging.FileHandler(_BOT_LOG, encoding="utf-8")
    bot_file_handler.setFormatter(fmt)

    err_file_handler = logging.FileHandler(_ERR_LOG, encoding="utf-8")
    err_file_handler.setLevel(logging.ERROR)
    err_file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    logging.basicConfig(
        level=lvl,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[console_handler, bot_file_handler, err_file_handler],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if rag_debug:
        logging.getLogger("aiogram").setLevel(logging.INFO)
        logging.getLogger("aiogram.dispatcher").setLevel(logging.INFO)
        logging.getLogger("aiogram.client.session").setLevel(logging.WARNING)
        logging.getLogger("aiogram.event").setLevel(logging.INFO)
        logging.getLogger(__name__).warning(
            "Подробный режим логов: корень DEBUG (RAG_INDEXER_DEBUG=1 или LOG_LEVEL=DEBUG); "
            "aiogram приглушён до INFO/WARNING. После отладки верните LOG_LEVEL=INFO и снимите RAG_INDEXER_DEBUG."
        )


async def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)
    bc = load_biblia_bot_config()
    bot = BotApplication(biblia_cfg=bc)
    try:
        await bot.initialize()
        logger.warning("Biblia: polling...")
        await bot.start()
    finally:
        await bot.close()


if __name__ == "__main__":
    try:
        validate_biblia_bot_startup(load_biblia_bot_config())
        validate_course_startup()
    except ValueError as e:
        logging.basicConfig(level=logging.INFO)
        log = logging.getLogger(__name__)
        log.error("%s", e)
        log.info(
            "Нужно в .env: BIBLIA_BOT_TOKEN, BIBLIA_DB_NAME, DB_*, OPENAI_API_KEY, "
            "DEEPSEEK_API_KEY; при COURSE_ENABLED — ACTIVE_PRODUCT из config/products.json"
        )
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
