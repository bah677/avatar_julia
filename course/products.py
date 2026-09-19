"""Реестр продуктов и жёсткий переключатель ACTIVE_PRODUCT."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

EXPERT_PRODUCT_ID = "_expert"
EXPERT_DISK_FOLDER = "00 Эксперт"

_CONFIG_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    aliases: tuple[str, ...] = ()
    disk_folder: str = ""

    def all_names(self) -> tuple[str, ...]:
        names = [self.name, self.id, *self.aliases]
        return tuple(n for n in names if n)


@dataclass
class ProductRegistry:
    products: List[Product] = field(default_factory=list)

    def by_id(self, product_id: str) -> Optional[Product]:
        pid = (product_id or "").strip()
        for p in self.products:
            if p.id == pid:
                return p
        return None

    def resolve_name(self, name: str) -> Optional[Product]:
        raw = (name or "").strip().casefold()
        if not raw:
            return None
        if raw in (EXPERT_PRODUCT_ID, "об эксперте", "эксперт"):
            return None
        for p in self.products:
            for alias in p.all_names():
                if alias.casefold() == raw:
                    return p
        return None

    def ids(self) -> List[str]:
        return [p.id for p in self.products]


def _default_products_path() -> Path:
    from config import config

    raw = (getattr(config, "PRODUCTS_FILE", "") or "config/products.json").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = _CONFIG_DIR / p
    return p


def load_product_registry(path: Optional[Path] = None) -> ProductRegistry:
    fp = path or _default_products_path()
    if not fp.is_file():
        raise FileNotFoundError(f"Реестр продуктов не найден: {fp}")
    data = json.loads(fp.read_text(encoding="utf-8"))
    items = data.get("products") if isinstance(data, dict) else data
    products: List[Product] = []
    seen: set[str] = set()
    for row in items or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not pid or not name:
            continue
        if pid == EXPERT_PRODUCT_ID:
            raise ValueError("id '_expert' зарезервирован и не может быть в реестре")
        if pid in seen:
            raise ValueError(f"Дубль product id: {pid}")
        seen.add(pid)
        aliases_raw = row.get("aliases") or []
        if isinstance(aliases_raw, str):
            aliases = tuple(a.strip() for a in aliases_raw.split(",") if a.strip())
        else:
            aliases = tuple(str(a).strip() for a in aliases_raw if str(a).strip())
        products.append(
            Product(
                id=pid,
                name=name,
                aliases=aliases,
                disk_folder=str(row.get("disk_folder") or name).strip(),
            )
        )
    if not products:
        raise ValueError(f"Пустой реестр продуктов: {fp}")
    return ProductRegistry(products=products)


@lru_cache(maxsize=1)
def get_registry() -> ProductRegistry:
    return load_product_registry()


def clear_registry_cache() -> None:
    get_registry.cache_clear()


def active_product_id() -> str:
    from config import config

    return (getattr(config, "ACTIVE_PRODUCT", "") or "").strip()


def active_product() -> Product:
    pid = active_product_id()
    p = get_registry().by_id(pid)
    if p is None:
        raise RuntimeError(f"ACTIVE_PRODUCT={pid!r} нет в реестре продуктов")
    return p


def scoped_product_ids(*, include_expert: bool = True) -> List[str]:
    ids = [active_product_id()]
    if include_expert and EXPERT_PRODUCT_ID not in ids:
        ids.append(EXPERT_PRODUCT_ID)
    return ids


def product_display_name(product_id: str) -> str:
    if product_id == EXPERT_PRODUCT_ID:
        return "Об эксперте"
    p = get_registry().by_id(product_id)
    return p.name if p else product_id


def folder_to_product_id(folder_name: str) -> Optional[str]:
    name = (folder_name or "").strip().rstrip("/")
    if name.casefold().startswith("00") and "эксперт" in name.casefold():
        return EXPERT_PRODUCT_ID
    if name == EXPERT_DISK_FOLDER:
        return EXPERT_PRODUCT_ID
    for p in get_registry().products:
        if p.disk_folder.casefold() == name.casefold():
            return p.id
    return None


def validate_course_startup() -> None:
    """Падает при старте, если COURSE_ENABLED и ACTIVE_PRODUCT некорректен."""
    from config import config

    if not getattr(config, "COURSE_ENABLED", False):
        return
    pid = (getattr(config, "ACTIVE_PRODUCT", "") or "").strip()
    if not pid:
        raise ValueError("COURSE_ENABLED=1: задайте ACTIVE_PRODUCT (id из config/products.json)")
    try:
        registry = load_product_registry()
    except Exception as e:
        raise ValueError(f"Не удалось загрузить реестр продуктов: {e}") from e
    if registry.by_id(pid) is None:
        known = ", ".join(registry.ids())
        raise ValueError(
            f"ACTIVE_PRODUCT={pid!r} нет в реестре. Доступны: {known}"
        )
    logger.info("Курс: активный продукт %s (%s)", pid, registry.by_id(pid).name)


def match_product_alias(value: str, aliases: Iterable[str] | None = None) -> Optional[str]:
    """Имя из старых метаданных Chroma → product_id."""
    raw = (value or "").strip()
    if not raw:
        return None
    p = get_registry().resolve_name(raw)
    return p.id if p else None
