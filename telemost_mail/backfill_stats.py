"""Догрузка материалов в RAG (почта / Я.Диск)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, List, Optional

logger = logging.getLogger(__name__)

NotifyFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class BackfillStats:
    source: str
    days: int
    scanned: int = 0
    skipped_cached: int = 0
    offered: int = 0
    indexed: int = 0
    ignored: int = 0
    errors: int = 0
    chunks: int = 0
    messages: List[str] = field(default_factory=list)

    def summary_html(self) -> str:
        from html import escape as html_escape

        lines = [
            f"<b>Догрузка RAG — {html_escape(self.source)}</b>",
            f"Период: последние <b>{self.days}</b> дн.",
            f"Просмотрено: <b>{self.scanned}</b>",
            f"Уже в кэше: <b>{self.skipped_cached}</b>",
        ]
        if self.source == "mail":
            lines.append(f"На решение админа: <b>{self.offered}</b>")
            lines.append(f"Загружено: <b>{self.indexed}</b>, игнор: <b>{self.ignored}</b>")
        else:
            lines.append(f"Новых файлов: <b>{self.indexed}</b>")
        lines.append(f"Ошибок: <b>{self.errors}</b>")
        lines.append(f"Чанков в RAG: <b>{self.chunks}</b>")
        for m in self.messages[:8]:
            lines.append(f"• {html_escape(m)}")
        return "\n".join(lines)
