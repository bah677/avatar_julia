"""Живой сбор паспортов эксперта / продукта / запуска: слоты + ход LLM."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from course.products import EXPERT_PRODUCT_ID, active_product_id, product_display_name

PASSPORT_KINDS = ("expert", "product", "launch")

PASSPORT_SPEC: Dict[str, Dict[str, Any]] = {
    "expert": {
        "title": "Паспорт эксперта",
        "store_product": EXPERT_PRODUCT_ID,
        "setting_key": "passport_expert",
        "slots": {
            "who": "кто она, роль, подход, ценности — своими словами",
            "audience": "с кем говорит, какой у этих людей язык",
            "tone": "как звучит в сторис: темп, тепло, жёсткость, юмор",
            "taboo": "чего не обещает, какие слова и позы запретны",
            "direct": "как зовёт в директ / на продукт, без канцелярита",
        },
        "rag_query": "об эксперте тон голос как говорит ценности табу",
    },
    "product": {
        "title": "Паспорт продукта",
        "store_product": None,
        "setting_key": "passport_product",
        "slots": {
            "for_whom": "для кого продукт и кому не подходит",
            "result": "какой результат обещает — только то, что эксперт подтвердил",
            "format": "формат: длительность, живое/записи, практики",
            "price": "цены, рассрочка, что входит — если сказала",
            "cta": "куда вести из сторис: слово в Direct, ссылка, квиз",
        },
        "rag_query": "о продукте программа аудитория результат цена CTA",
    },
    "launch": {
        "title": "Паспорт запуска",
        "store_product": None,
        "setting_key": "passport_launch",
        "slots": {
            "dates": "даты: прогрев, окно продаж, эфиры, когда закрываем",
            "offer": "оффер этого потока одним абзацем",
            "cta": "слово в директ / ссылка / куда писать прямо сейчас",
            "objections": "главные возражения и как на них отвечает",
            "proof": "соцдоказ: отзывы, разборы, «как было в прошлом потоке»",
            "forbidden": "что нельзя обещать в этом запуске",
        },
        "rag_query": "запуск набор поток продажи оффер даты CTA",
    },
}


def spec_for(kind: str) -> Dict[str, Any]:
    k = (kind or "").strip()
    if k not in PASSPORT_SPEC:
        raise ValueError(f"unknown passport kind: {kind}")
    return PASSPORT_SPEC[k]


def store_product_id(kind: str) -> str:
    spec = spec_for(kind)
    return spec["store_product"] or active_product_id()


def empty_slots(kind: str) -> Dict[str, str]:
    return {key: "" for key in spec_for(kind)["slots"]}


def merge_slots(kind: str, current: Dict[str, str], patch: Dict[str, Any]) -> Dict[str, str]:
    out = empty_slots(kind)
    out.update({k: str(v or "").strip() for k, v in (current or {}).items() if k in out})
    for k, v in (patch or {}).items():
        if k not in out:
            continue
        text = str(v or "").strip()
        if text:
            out[k] = text
    return out


def filled_count(slots: Dict[str, str]) -> tuple[int, int]:
    keys = list(slots.keys())
    n = sum(1 for k in keys if (slots.get(k) or "").strip())
    return n, len(keys)


def missing_slots(kind: str, slots: Dict[str, str]) -> List[str]:
    spec = spec_for(kind)
    out = []
    for key, hint in spec["slots"].items():
        if not (slots.get(key) or "").strip():
            out.append(f"{key}: {hint}")
    return out


def slots_to_markdown(kind: str, slots: Dict[str, str]) -> str:
    spec = spec_for(kind)
    lines = [f"# {spec['title']} · {product_display_name(store_product_id(kind))}", ""]
    for key, hint in spec["slots"].items():
        val = (slots.get(key) or "").strip() or "—"
        lines.append(f"## {key}")
        lines.append(hint)
        lines.append("")
        lines.append(val)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def decode_setting(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, memoryview)):
        raw = bytes(raw).decode("utf-8")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def load_passport(stor, kind: str) -> Dict[str, Any]:
    spec = spec_for(kind)
    pid = store_product_id(kind)
    raw = await stor.get_content_setting(pid, spec["setting_key"])
    data = decode_setting(raw)
    slots = merge_slots(kind, data.get("slots") or {}, {})
    return {
        "slots": slots,
        "text": (data.get("text") or "").strip(),
        "done": bool(data.get("done")),
    }


async def save_passport(
    stor,
    kind: str,
    slots: Dict[str, str],
    *,
    done: bool,
    user_id: int = 0,
) -> Dict[str, Any]:
    spec = spec_for(kind)
    pid = store_product_id(kind)
    text = slots_to_markdown(kind, slots)
    payload = {
        "slots": slots,
        "text": text,
        "done": bool(done),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": int(user_id or 0),
    }
    await stor.set_content_setting(pid, spec["setting_key"], payload)
    if kind == "expert" and any(slots.values()):
        await stor.insert_style_profile(
            product_id=active_product_id(),
            text=text,
            origin="manual",
            created_by=user_id or None,
            activate=True,
        )
    return payload


def build_turn_system(kind: str, slots: Dict[str, str], rag_digest: str) -> str:
    spec = spec_for(kind)
    missing = missing_slots(kind, slots)
    filled = {k: v for k, v in slots.items() if (v or "").strip()}
    return f"""Ты — умный продюсер. Собираешь {spec['title']} в живом разговоре с экспертом.

Это НЕ интервью по шаблону. Не задавай список вопросов. Не повторяй то, что уже ясно из слотов или из базы.

Уже знаем (слоты):
{json.dumps(filled, ensure_ascii=False, indent=2) or '{}'}

Ещё пусто:
{json.dumps(missing, ensure_ascii=False) if missing else 'ничего критичного'}

Фрагменты из базы RAG (могут быть неточны — сверяй с тем, что говорит эксперт):
{rag_digest or '(пусто)'}

Правила:
- Один короткий ход: 1–3 предложения живым языком + максимум ОДИН вопрос, и только про самое важное из пустого.
- Если эксперт уже ответила — сразу впиши в слоты своими словами, не переспрашивай то же.
- Если в RAG есть факт, а эксперт молчит — коротко перескажи и спроси: «так и оставляем?»
- Не выдумывай цены, даты, обещания. Нет в речи и в RAG — слот пустой.
- Когда слотов достаточно для работы (не обязательно 100%) — поставь done=true и коротко скажи, что сохранишь.

Верни JSON:
{{"reply":"текст для эксперта","slots":{{"ключ":"значение только если появилось новое"}},"done":false}}
Ключи slots — только из списка: {", ".join(spec["slots"])}.
"""
