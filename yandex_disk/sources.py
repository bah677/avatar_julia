"""Загрузка описаний папок Яндекс.Диска из JSON."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class YandexDiskSource:
    """Одна папка на Диске с правилами импорта в RAG."""

    id: str
    path: str
    masks: List[str] = field(default_factory=lambda: ["*.mp3"])
    hint: str = ""
    default_product: str = ""
    default_content_type: str = ""
    recursive: bool = False
    enabled: bool = True
    #: запасные пути, если основной дал 404 / пусто
    alternate_paths: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["YandexDiskSource"]:
        if not isinstance(data, dict):
            return None
        sid = str(data.get("id") or "").strip()
        path = str(data.get("path") or "").strip()
        if not sid or not path:
            return None
        masks_raw = data.get("masks") or ["*.mp3"]
        if isinstance(masks_raw, str):
            masks = [m.strip() for m in masks_raw.split(",") if m.strip()]
        else:
            masks = [str(m).strip() for m in masks_raw if str(m).strip()]
        alts_raw = data.get("alternate_paths") or data.get("path_fallbacks") or []
        if isinstance(alts_raw, str):
            alts = [a.strip() for a in alts_raw.split(",") if a.strip()]
        else:
            alts = [str(a).strip() for a in alts_raw if str(a).strip()]
        alts = [a if a.startswith("/") else f"/{a}" for a in alts]
        return cls(
            id=sid,
            path=path if path.startswith("/") else f"/{path}",
            masks=masks or ["*.mp3"],
            hint=str(data.get("hint") or data.get("description") or "").strip(),
            default_product=str(data.get("default_product") or "").strip(),
            default_content_type=str(data.get("default_content_type") or "").strip(),
            recursive=bool(data.get("recursive", False)),
            enabled=bool(data.get("enabled", True)),
            alternate_paths=alts,
        )


def load_yandex_disk_sources(
    *,
    json_inline: str = "",
    json_file: str = "",
    project_root: Optional[Path] = None,
) -> List[YandexDiskSource]:
    """
    Источники из ``YANDEX_DISK_SOURCES`` (JSON) или файла ``YANDEX_DISK_SOURCES_FILE``.
    """
    raw = (json_inline or "").strip()
    if not raw and (json_file or "").strip():
        fp = Path(json_file.strip())
        if not fp.is_absolute() and project_root:
            fp = project_root / fp
        if fp.is_file():
            raw = fp.read_text(encoding="utf-8").strip()
        else:
            logger.warning("Yandex Disk sources file not found: %s", fp)

    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("YANDEX_DISK_SOURCES: invalid JSON: %s", e)
        return []

    items = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(items, list):
        logger.error("YANDEX_DISK_SOURCES: ожидается {\"sources\": [...]}")
        return []

    out: List[YandexDiskSource] = []
    for item in items:
        src = YandexDiskSource.from_dict(item)
        if src and src.enabled:
            out.append(src)
    return out
