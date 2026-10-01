"""Сопоставить текст пользователя со списком уроков / номером модуля."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence


_MODULE_RE = re.compile(
    r"(?:модул[ьяею]|раздел[аеу]?|module)\s*(\d{1,2})",
    re.IGNORECASE,
)
_LESSON_KEY_RE = re.compile(r"(?<!\d)(\d{1,2})[._](\d{1,2})(?!\d)")


@dataclass
class ScopeMatch:
    lesson_key: str = ""
    module_no: Optional[int] = None
    kind_hint: str = ""
    confidence: float = 0.0
    title: str = ""


def parse_module_no(text: str) -> Optional[int]:
    m = _MODULE_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1))


def _norm(s: str) -> str:
    t = (s or "").casefold().replace("ё", "е")
    t = re.sub(r"[^\w\s]+", " ", t, flags=re.U)
    return re.sub(r"\s+", " ", t).strip()


def match_scope(text: str, lessons: Sequence[dict]) -> ScopeMatch:
    blob = text or ""
    kind = ""
    low = blob.casefold()
    if re.search(r"практик|zoom|зум", low):
        kind = "practice"
    elif "эфир" in low:
        kind = "broadcast"
    elif "урок" in low:
        kind = "lesson_video"

    module_no = parse_module_no(blob)
    mkey = _LESSON_KEY_RE.search(blob)
    if mkey:
        key = f"{int(mkey.group(1))}.{int(mkey.group(2))}"
        title = ""
        for row in lessons:
            if str(row.get("lesson_key")) == key:
                title = row.get("title") or ""
                if module_no is None:
                    module_no = row.get("module_no")
                break
        return ScopeMatch(
            lesson_key=key,
            module_no=module_no,
            kind_hint=kind,
            confidence=0.9,
            title=title,
        )

    q = _norm(blob)
    # выкинуть служебные слова, чтобы искать по названию урока
    q_words = [
        w
        for w in q.split()
        if w not in {
            "урок", "урока", "практика", "практики", "эфир", "модуль", "модуля",
            "раздел", "раздела", "zoom", "зум", "запись", "видео", "ссылка",
            "kinescope", "vimeo", "youtube", "на", "к", "про", "это",
        }
        and not w.isdigit()
        and "http" not in w
        and "kinescope" not in w
        and "youtu" not in w
        and "vimeo" not in w
        and "zoom" not in w
    ]
    best: Optional[ScopeMatch] = None
    best_score = 0.0
    for row in lessons:
        title = _norm(str(row.get("title") or ""))
        key = str(row.get("lesson_key") or "")
        if not title:
            continue
        title_words = set(title.split())
        if not title_words:
            continue
        hits = sum(1 for w in q_words if w in title or w in title_words)
        if hits <= 0:
            continue
        score = hits / max(1, len(q_words) or 1)
        if len(q_words) == 1 and q_words[0] in title:
            score = max(score, 0.75)
        if score > best_score:
            best_score = score
            best = ScopeMatch(
                lesson_key=key,
                module_no=row.get("module_no") if module_no is None else module_no,
                kind_hint=kind,
                confidence=min(0.88, 0.45 + score * 0.5),
                title=row.get("title") or "",
            )
    if best and best_score >= 0.4:
        return best
    if module_no is not None:
        return ScopeMatch(
            module_no=module_no,
            kind_hint=kind or "practice",
            confidence=0.8,
        )
    return ScopeMatch(kind_hint=kind, confidence=0.3 if kind else 0.0)
