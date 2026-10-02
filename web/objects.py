"""Дерево объектов контекста для веб-студии и разбор их идентификаторов."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from course.passports import PASSPORT_KINDS, filled_count, load_passport, spec_for
from course.products import EXPERT_PRODUCT_ID, active_product_id, product_display_name

logger = logging.getLogger(__name__)

KIND_LABELS: Dict[str, str] = {
    "lesson_video": "Видео урока",
    "practice": "Практика (Zoom)",
    "broadcast": "Эфир",
    "summary": "Конспект",
    "slides": "Слайды",
    "extra": "Доп. материал",
    "post": "Пост эксперта",
    "expert_info": "Об эксперте",
    "product_info": "О продукте",
    "other": "Материал",
}

# Живой чат: chat_id в метаданные чанков не пишется, поэтому объекты — это
# срезы по тому, что в них есть: вся переписка, только участники, только эксперт.
LIVE_OBJECTS: List[Tuple[str, str, str, Dict[str, Any]]] = [
    (
        "chat",
        "Переписка в чате",
        "Всё, что бот прочитал в группах",
        {"origin": "telegram_group"},
    ),
    (
        "client",
        "Реплики участников",
        "Голос аудитории: вопросы, боли, живые формулировки",
        {"origin": "telegram_group", "role": "client"},
    ),
    (
        "expert",
        "Ответы эксперта в чате",
        "Как эксперт отвечает людям",
        {"origin": "telegram_group", "role": "expert"},
    ),
    (
        "testimonial",
        "Отзывы участников",
        "Цитаты и соцдоказательства",
        {"source_kind": "testimonial"},
    ),
]

LIVE_KEYS = tuple(k for k, _, _, _ in LIVE_OBJECTS)
LIVE_FILTERS: Dict[str, Dict[str, Any]] = {k: f for k, _, _, f in LIVE_OBJECTS}
LIVE_NAMES: Dict[str, str] = {k: n for k, n, _, _ in LIVE_OBJECTS}

#: Старые идентификаторы из сохранённых чатов.
LIVE_ALIASES = {"dialog": "chat", "expert_reply": "expert"}

_LESSON_SOURCE_ORDER = {
    "summary": 0,
    "slides": 1,
    "lesson_video": 2,
    "practice": 3,
    "broadcast": 4,
    "extra": 5,
}

_NO_LESSON_RECORDING_KINDS = ("practice", "broadcast", "lesson_video")

READY_STATUSES = ("done",)
BUSY_STATUSES = ("new", "fetching", "extracted", "indexed", "mining")


@dataclass(frozen=True)
class ObjectRef:
    """Разобранный идентификатор объекта: `source:<uuid>`, `lesson:2.4`, …"""

    kind: str  # source | lesson | passport | live
    value: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.value}"


def parse_object_id(raw: str) -> Optional[ObjectRef]:
    text = (raw or "").strip()
    if ":" not in text:
        return None
    kind, value = text.split(":", 1)
    kind = kind.strip().lower()
    value = value.strip()
    if not value:
        return None
    if kind == "source":
        try:
            UUID(value)
        except ValueError:
            return None
        return ObjectRef("source", value)
    if kind == "lesson":
        return ObjectRef("lesson", value)
    if kind == "passport" and value in PASSPORT_KINDS:
        return ObjectRef("passport", value)
    if kind == "live":
        value = LIVE_ALIASES.get(value, value)
        if value in LIVE_KEYS:
            return ObjectRef("live", value)
    return None


def parse_object_ids(raw: Sequence[str]) -> List[ObjectRef]:
    out: List[ObjectRef] = []
    seen: set[str] = set()
    for item in raw or []:
        ref = parse_object_id(str(item))
        if ref and ref.id not in seen:
            seen.add(ref.id)
            out.append(ref)
    return out


def source_label(src: Dict[str, Any]) -> str:
    """Короткое человекочитаемое имя источника — его же эксперт пишет в чате."""
    kind = str(src.get("kind") or "other")
    title = (src.get("title") or "").strip()
    base = KIND_LABELS.get(kind, kind)
    date = src.get("recorded_on")
    date_str = date.strftime("%d.%m.%Y") if hasattr(date, "strftime") else ""
    if title:
        name = title if base.split()[0].lower() in title.lower() else f"{base}: {title}"
    else:
        name = base
    if date_str and date_str not in name:
        name = f"{name} · {date_str}"
    return name[:160]


def source_status(src: Dict[str, Any]) -> str:
    status = str(src.get("status") or "")
    if status in READY_STATUSES:
        return "ready"
    if status == "error":
        return "error"
    if status in BUSY_STATUSES:
        return "busy"
    return "off"


def _duration_text(sec: Optional[int]) -> str:
    try:
        total = int(sec or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    h, rest = divmod(total, 3600)
    m = rest // 60
    return f"{h}:{m:02d}" if h else f"{m} мин"


def _source_meta(src: Dict[str, Any], cards: int) -> str:
    bits: List[str] = []
    dur = _duration_text(src.get("duration_sec"))
    if dur:
        bits.append(dur)
    if cards:
        bits.append(f"{cards} идей")
    status = source_status(src)
    if status == "busy":
        bits.append("в обработке")
    elif status == "error":
        reason = str(src.get("error_message") or "").strip()
        bits.append(f"не обработан: {reason[:90]}" if reason else "не обработан")
    if src.get("lesson_id") is None and src.get("module_no") is not None:
        bits.append(f"модуль {src['module_no']}")
    origin = str(src.get("origin") or "")
    if origin and origin != "disk":
        bits.append(origin)
    return " · ".join(bits)


def _source_node(src: Dict[str, Any], cards: int) -> Dict[str, Any]:
    status = source_status(src)
    return {
        "id": f"source:{src['id']}",
        "kind": "source",
        "name": source_label(src),
        "meta": _source_meta(src, cards),
        "status": status,
        "selectable": status == "ready",
        "source_kind": src.get("kind") or "other",
    }


async def _cards_by_source(stor, sources: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for src in sources:
        try:
            out[str(src["id"])] = await stor.count_cards_for_source(src["id"])
        except Exception as e:  # pragma: no cover — счётчик не критичен
            logger.debug("count_cards_for_source %s: %s", src.get("id"), e)
            out[str(src["id"])] = 0
    return out


async def _live_counts(app) -> Dict[str, int]:
    """Сколько фрагментов живого чата в индексе — по срезам."""
    out = {key: 0 for key in LIVE_KEYS}
    rag = getattr(app, "rag_stack", None)
    if rag is None:
        return out
    try:
        coll = rag.vectors.expert_collection
        raw = coll.get(
            where={"origin": "telegram_group"}, include=["metadatas"], limit=5000
        )
        metas = raw.get("metadatas") or []
        out["chat"] = len(metas)
        out["client"] = sum(1 for m in metas if str((m or {}).get("role") or "") == "client")
        out["expert"] = sum(1 for m in metas if str((m or {}).get("role") or "") == "expert")
        raw_t = coll.get(
            where={"source_kind": "testimonial"}, include=["metadatas"], limit=5000
        )
        out["testimonial"] = len(raw_t.get("metadatas") or [])
    except Exception as e:
        logger.warning("live counts: %s", e)
    return out


async def build_tree(app) -> Dict[str, Any]:
    """Группы объектов: паспорта, уроки с источниками, записи без урока, живые чаты."""
    stor = app.user_storage
    pid = active_product_id()
    lessons = await stor.list_course_lessons(pid)
    # В дереве только обработанные источники: всё остальное (ошибки, очередь,
    # удалённое) не даст материала в контекст и видно в блоке «Очередь обработки».
    sources = await stor.list_course_sources_by_product(
        [pid, EXPERT_PRODUCT_ID], statuses=list(READY_STATUSES)
    )
    counts = await _cards_by_source(stor, sources)

    groups: List[Dict[str, Any]] = []

    # ── Паспорта ────────────────────────────────────────────────────────────
    passport_items: List[Dict[str, Any]] = []
    for kind in PASSPORT_KINDS:
        spec = spec_for(kind)
        data = await load_passport(stor, kind)
        done, total = filled_count(data.get("slots") or {})
        if not done:
            continue  # пустой паспорт в контекст не положишь
        passport_items.append(
            {
                "id": f"passport:{kind}",
                "kind": "passport",
                "name": spec["title"],
                "meta": f"{done}/{total} заполнено",
                "status": "ready",
                "selectable": True,
            }
        )
    if passport_items:
        groups.append({"id": "passports", "title": "Паспорта", "items": passport_items})

    # ── Уроки ───────────────────────────────────────────────────────────────
    by_lesson: Dict[int, List[Dict[str, Any]]] = {}
    free: List[Dict[str, Any]] = []
    for src in sources:
        lid = src.get("lesson_id")
        if lid:
            by_lesson.setdefault(int(lid), []).append(src)
        else:
            free.append(src)

    modules: Dict[Any, Dict[str, Any]] = {}
    for les in lessons:
        children = sorted(
            by_lesson.get(int(les["id"]), []),
            key=lambda s: (_LESSON_SOURCE_ORDER.get(str(s.get("kind")), 9), str(s.get("title") or "")),
        )
        have = {str(c.get("kind")) for c in children}
        marks = []
        if "summary" in have:
            marks.append("конспект")
        if "slides" in have:
            marks.append("слайды")
        if "lesson_video" in have:
            marks.append("видео")
        practices = sum(1 for c in children if c.get("kind") == "practice")
        if practices:
            marks.append(f"практик {practices}")
        cards_total = sum(counts.get(str(c["id"]), 0) for c in children)
        if cards_total:
            marks.append(f"{cards_total} идей")
        has_passport = bool((les.get("passport_text") or "").strip())
        if not children and not has_passport:
            continue  # урок пустой: ни обработанных материалов, ни паспорта
        if has_passport and not children:
            marks.append("только паспорт")
        node = {
            "id": f"lesson:{les['lesson_key']}",
            "kind": "lesson",
            "name": f"Урок {les['lesson_key']}. {les.get('title') or ''}".strip().rstrip("."),
            "meta": " · ".join(marks),
            "status": "ready",
            "selectable": True,
            "children": [
                {**_source_node(c, counts.get(str(c["id"]), 0)), "parent": f"lesson:{les['lesson_key']}"}
                for c in children
            ],
        }
        mod_no = les.get("module_no")
        key = mod_no if mod_no is not None else "—"
        mod = modules.setdefault(
            key,
            {
                "id": f"module:{key}",
                "title": f"Модуль {mod_no}" if mod_no is not None else "Без модуля",
                "lessons": [],
            },
        )
        mod["lessons"].append(node)

    if modules:
        ordered = sorted(
            modules.items(),
            key=lambda kv: (kv[0] == "—", kv[0] if isinstance(kv[0], int) else 0),
        )
        groups.append(
            {
                "id": "lessons",
                "title": "Уроки",
                "modules": [m for _, m in ordered],
            }
        )

    # ── Записи без урока и прочие материалы ────────────────────────────────
    recordings = [s for s in free if str(s.get("kind")) in _NO_LESSON_RECORDING_KINDS]
    others = [s for s in free if str(s.get("kind")) not in _NO_LESSON_RECORDING_KINDS]
    if recordings:
        recordings.sort(key=lambda s: (s.get("recorded_on") is None, s.get("recorded_on") or "", str(s.get("title") or "")), reverse=True)
        groups.append(
            {
                "id": "recordings",
                "title": "Записи без урока",
                "items": [_source_node(s, counts.get(str(s["id"]), 0)) for s in recordings],
            }
        )
    if others:
        groups.append(
            {
                "id": "other",
                "title": "Прочие материалы",
                "items": [_source_node(s, counts.get(str(s["id"]), 0)) for s in others],
            }
        )

    # ── Живой чат ──────────────────────────────────────────────────────────
    live_counts = await _live_counts(app)
    live_items: List[Dict[str, Any]] = []
    if live_counts.get("chat"):
        children = [
            {
                "id": f"live:{key}",
                "kind": "live",
                "name": LIVE_NAMES[key],
                "meta": f"{live_counts[key]} фрагментов · {hint}",
                "status": "ready",
                "selectable": True,
                "parent": "live:chat",
            }
            for key, _, hint, _ in LIVE_OBJECTS
            if key in ("client", "expert") and live_counts.get(key)
        ]
        live_items.append(
            {
                "id": "live:chat",
                "kind": "live",
                "name": LIVE_NAMES["chat"],
                "meta": f"{live_counts['chat']} фрагментов · всё, что бот прочитал в группах",
                "status": "ready",
                "selectable": True,
                "children": children,
            }
        )
    if live_counts.get("testimonial"):
        live_items.append(
            {
                "id": "live:testimonial",
                "kind": "live",
                "name": LIVE_NAMES["testimonial"],
                "meta": f"{live_counts['testimonial']} фрагментов · цитаты и соцдоказательства",
                "status": "ready",
                "selectable": True,
            }
        )
    if live_items:
        groups.append({"id": "live", "title": "Живой чат", "items": live_items})

    return {
        "product": {"id": pid, "name": product_display_name(pid)},
        "groups": groups,
    }


def object_names(tree: Dict[str, Any]) -> Dict[str, str]:
    """`source:<uuid>` → отображаемое имя (для подписи контекста в промпте)."""
    out: Dict[str, str] = {}

    def _walk(items: Sequence[Dict[str, Any]]) -> None:
        for item in items or []:
            if item.get("id"):
                out[str(item["id"])] = str(item.get("name") or item["id"])
            _walk(item.get("children") or [])

    for group in tree.get("groups") or []:
        _walk(group.get("items") or [])
        for mod in group.get("modules") or []:
            _walk(mod.get("lessons") or [])
    return out
