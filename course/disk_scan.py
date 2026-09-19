"""Обход Диска и сверка с course_sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from course.disk_layout import DiskFileRole, classify_disk_path
from course.products import EXPERT_PRODUCT_ID, active_product, get_registry
from yandex_disk.webdav import RemoteFile, YandexDiskWebDAV

logger = logging.getLogger(__name__)


@dataclass
class DiskScanItem:
    remote: RemoteFile
    role: DiskFileRole


@dataclass
class DiskScanResult:
    items: List[DiskScanItem] = field(default_factory=list)
    skipped: int = 0
    errors: List[str] = field(default_factory=list)


def _fresh_enough(rf: RemoteFile, *, min_age_sec: int = 120) -> bool:
    mod = rf.modified
    if mod is None:
        return True
    if getattr(mod, "tzinfo", None) is None:
        mod = mod.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - mod
    return age >= timedelta(seconds=min_age_sec)


async def scan_course_disk(
    webdav: YandexDiskWebDAV,
    *,
    disk_root: str,
    active_product_id: str,
) -> DiskScanResult:
    registry = get_registry()
    product = registry.by_id(active_product_id)
    folders = []
    expert = f"{disk_root.rstrip('/')}/00 Эксперт"
    folders.append(expert)
    if product:
        folders.append(f"{disk_root.rstrip('/')}/{product.disk_folder}")

    result = DiskScanResult()
    seen_paths: set[str] = set()
    for folder in folders:
        try:
            files = await webdav.list_files(folder, recursive=True)
        except Exception as e:
            logger.warning("disk scan %s: %s", folder, e)
            result.errors.append(f"{folder}: {e}")
            continue
        for rf in files:
            if rf.path in seen_paths:
                continue
            seen_paths.add(rf.path)
            role = classify_disk_path(
                rf.path,
                active_product_id=active_product_id,
                registry=registry,
                course_disk_root=disk_root,
            )
            if role.skip:
                result.skipped += 1
                continue
            if not _fresh_enough(rf):
                result.skipped += 1
                continue
            result.items.append(DiskScanItem(remote=rf, role=role))
    return result
