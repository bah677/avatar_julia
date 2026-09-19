"""Dataclass'ы контура курса — без зависимостей от Telegram."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import UUID


SOURCE_KINDS = (
    "lesson_video",
    "practice",
    "broadcast",
    "summary",
    "slides",
    "extra",
    "other",
    "post",
    "expert_info",
    "product_info",
)

SOURCE_STATUSES = (
    "new",
    "fetching",
    "extracted",
    "indexed",
    "mining",
    "done",
    "error",
    "skipped",
    "deleted",
)

VIDEO_ORIGINS = ("youtube", "vimeo", "kinescope", "disk")

KIND_LABELS = {
    "lesson_video": "видео урока",
    "practice": "практика",
    "broadcast": "эфир",
    "summary": "конспект",
    "slides": "слайды",
    "extra": "доп. материал",
    "other": "другое",
    "post": "пост",
    "expert_info": "об эксперте",
    "product_info": "о продукте",
}


@dataclass
class Lesson:
    id: Optional[int] = None
    product_id: str = ""
    lesson_key: str = ""
    module_no: Optional[int] = None
    lesson_no: int = 0
    title: str = ""
    disk_path: Optional[str] = None
    passport: Dict[str, Any] = field(default_factory=dict)
    passport_text: str = ""


@dataclass
class Source:
    id: Optional[UUID] = None
    product_id: str = ""
    origin: str = "disk"
    kind: str = "other"
    lesson_id: Optional[int] = None
    title: str = ""
    disk_path: Optional[str] = None
    disk_etag: str = ""
    video_id: Optional[str] = None
    url: str = ""
    alt_urls: List[str] = field(default_factory=list)
    platform: str = ""
    duration_sec: Optional[int] = None
    recorded_on: Optional[date] = None
    status: str = "new"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Card:
    id: Optional[UUID] = None
    product_id: str = ""
    source_id: Optional[UUID] = None
    lesson_id: Optional[int] = None
    type: str = "idea"
    title: str = ""
    text: str = ""
    quote: str = ""
    anchor_sec: Optional[float] = None
    page: Optional[int] = None
    speaker: str = "expert"
    audience_pain: str = ""
    funnel_stage: str = "warmup"
    formats: List[str] = field(default_factory=list)
    score: float = 0.0
    frequency: int = 1
    status: str = "active"


@dataclass(frozen=True)
class FormatSpec:
    id: str
    platform: str
    title: str
    min_chars: int = 0
    max_chars: int = 0
    structure: str = ""
    markup: str = "plain"
    extra_rules: str = ""


@dataclass
class PlanItem:
    format: str
    card_ids: List[UUID] = field(default_factory=list)
    angle: str = ""
    funnel_stage: str = "warmup"
    planned_day: Optional[int] = None
    lesson_id: Optional[int] = None


@dataclass
class VideoProbe:
    host: str
    video_id: str
    url: str
    title: str = ""
    duration_sec: Optional[int] = None
    recorded_on: Optional[date] = None
    description: str = ""
    chapters: List[Dict[str, Any]] = field(default_factory=list)
    has_subtitles: bool = False
    playlist_index: Optional[int] = None


@dataclass
class ClassifyResult:
    lesson_key: str = ""
    kind: str = "other"
    recorded_on: Optional[date] = None
    confidence: float = 0.0
    title: str = ""
    source: str = "rules"  # rules | llm
