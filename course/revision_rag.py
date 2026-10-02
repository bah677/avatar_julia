"""Поиск в RAG перед правкой черновика: чанки источника + при нужде отзывы."""

from __future__ import annotations

from typing import Any, List, Sequence

from rag.retrieval_planner import CONTENT_CATEGORY_TESTIMONIAL

_CLIENT_VOICE_HINTS = (
    "цитат",
    "участниц",
    "участник",
    "отзыв",
    "из чата",
    "созвон",
    "практик",
    "зум",
    "клиент",
    "живые",
    "живую",
    "реплик",
    "инсайт",
)


def revision_search_query(
    *,
    instruction: str,
    task: str = "",
    card_titles: Sequence[str] = (),
) -> str:
    parts: List[str] = []
    inst = (instruction or "").strip()
    if inst:
        parts.append(inst)
    t = (task or "").strip()
    if t:
        parts.append(t)
    titles = [str(x).strip() for x in card_titles if str(x).strip()]
    if titles:
        parts.append("Темы: " + "; ".join(titles[:5]))
    return "\n".join(parts)


def wants_client_voice(instruction: str) -> bool:
    low = (instruction or "").casefold()
    return any(h in low for h in _CLIENT_VOICE_HINTS)


def retrieve_for_revision(
    gateway: Any,
    *,
    query: str,
    source_ids: Sequence[str] = (),
    include_testimonials: bool = False,
    k_source: int = 6,
    k_broad: int = 5,
    k_testimonials: int = 3,
    max_chars: int = 8000,
) -> str:
    q = (query or "").strip()
    if not q or gateway is None:
        return ""
    hits: List[dict] = []
    sids = [str(x).strip() for x in source_ids if str(x).strip()]
    if sids:
        hits.extend(gateway.search_chunks(q, k=k_source, source_ids=sids))
    hits.extend(gateway.search_chunks(q, k=k_broad))
    if include_testimonials:
        hits.extend(
            gateway.search_chunks(
                q,
                k=k_testimonials,
                content_category=CONTENT_CATEGORY_TESTIMONIAL,
            )
        )
    seen: set = set()
    uniq: List[dict] = []
    for h in hits:
        hid = h.get("id")
        if hid in seen:
            continue
        seen.add(hid)
        uniq.append(h)
    text = gateway.format_chunks_for_prompt(uniq, max_per_source=4)
    if len(text) > max_chars:
        cut = text[:max_chars]
        nl = cut.rfind("\n")
        text = cut[:nl] if nl > max_chars // 2 else cut
    return text.strip()
