"""Ожидание решения админа по письму Телемост (догрузка и live)."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_events: Dict[str, asyncio.Event] = {}
_outcomes: Dict[str, str] = {}


def register_mail_decision_wait(pending_id: str) -> None:
    _events[pending_id] = asyncio.Event()


async def wait_mail_decision(
    pending_id: str,
    *,
    timeout_sec: float = 86_400,
) -> Optional[str]:
    """``load`` | ``ignore`` | None при таймауте."""
    pid = (pending_id or "").strip()
    if not pid:
        return None
    if pid not in _events:
        register_mail_decision_wait(pid)
    try:
        await asyncio.wait_for(_events[pid].wait(), timeout=timeout_sec)
        return _outcomes.get(pid)
    except asyncio.TimeoutError:
        logger.warning("wait_mail_decision timeout pending_id=%s", pid)
        return None
    finally:
        _events.pop(pid, None)
        _outcomes.pop(pid, None)


def resolve_mail_decision(pending_id: str, outcome: str) -> None:
    pid = (pending_id or "").strip()
    if not pid:
        return
    _outcomes[pid] = (outcome or "").strip()
    ev = _events.get(pid)
    if ev:
        ev.set()
