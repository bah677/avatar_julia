"""
Конфигурация автономного проекта «БиблияБот».

- BibliaBotConfig — токен бота, имя БД и проверки перед стартом (main.py).
- config (AppConfig) — то, что ждут модули bot/* / openai_client: админка, YooKassa,
  нули для клуб-полей, если в боте Библии они не используются.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from dotenv import load_dotenv

_CONFIG_DIR = Path(__file__).resolve().parent
# Явно подгружаем `.env` из каталога проекта (рядом с этим файлом),
# чтобы переменные находились независимо от текущей рабочей директории.
load_dotenv(_CONFIG_DIR / ".env")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Процесс бота «Библия» (точка входа main.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BibliaBotConfig:
    """Токен бота, имя базы, общий Postgres и OpenAI."""

    BIBLIA_BOT_TOKEN: str
    BIBLIA_DB_NAME: str
    OPENAI_API_KEY: str
    DB_HOST: str
    DB_PORT: str
    DB_USER: str
    DB_PASSWORD: str
    LOG_LEVEL: str = "INFO"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.BIBLIA_DB_NAME}"
        )


def validate_biblia_bot_startup(bc: BibliaBotConfig) -> None:
    req = {
        "BIBLIA_BOT_TOKEN": bc.BIBLIA_BOT_TOKEN,
        "BIBLIA_DB_NAME": bc.BIBLIA_DB_NAME,
        "OPENAI_API_KEY": bc.OPENAI_API_KEY,
        "DB_HOST": bc.DB_HOST,
        "DB_PORT": bc.DB_PORT,
        "DB_USER": bc.DB_USER,
        "DB_PASSWORD": bc.DB_PASSWORD,
    }
    miss = [k for k, v in req.items() if not str(v).strip()]
    if miss:
        raise ValueError(f"Biblia: не заданы переменные: {', '.join(miss)}")
    if not (os.getenv("DEEPSEEK_API_KEY") or "").strip():
        raise ValueError("Biblia: нужен DEEPSEEK_API_KEY для агента")


def _biblia_db_name() -> str:
    """Имя БД: BIBLIA_DB_NAME или fallback на DB_NAME (как в общем .env)."""
    a = (os.getenv("BIBLIA_DB_NAME") or "").strip()
    if a:
        return a
    return (os.getenv("DB_NAME") or "").strip()


def load_biblia_bot_config() -> BibliaBotConfig:
    return BibliaBotConfig(
        BIBLIA_BOT_TOKEN=os.getenv("BIBLIA_BOT_TOKEN", "").strip(),
        BIBLIA_DB_NAME=_biblia_db_name(),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "").strip(),
        DB_HOST=os.getenv("DB_HOST", "localhost"),
        DB_PORT=os.getenv("DB_PORT", ""),
        DB_USER=os.getenv("DB_USER", ""),
        DB_PASSWORD=os.getenv("DB_PASSWORD", ""),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
    )


# ---------------------------------------------------------------------------
# Общий runtime-конфиг для слоя bot/* (без обязательного MIRON_BOT_TOKEN)
# ---------------------------------------------------------------------------


def _parse_gift_link_validity_days(raw: Optional[str]) -> int:
    if raw is None or not str(raw).strip():
        return 30
    try:
        n = int(str(raw).strip(), 10)
    except ValueError:
        return 30
    return max(1, min(3650, n))


def _normalize_env_username(raw: str) -> Optional[str]:
    if not raw or not raw.strip():
        return None
    return raw.strip().lstrip("@") or None


@dataclass(frozen=True)
class AppConfig:
    MIRON_BOT_TOKEN: str = ""
    OPENAI_API_KEY: str = ""

    ADMIN_BOT_TOKEN: Optional[str] = None  # legacy: только если нет BIBLIA_BOT_TOKEN
    BIBLIA_BOT_TOKEN: Optional[str] = None  # основной бот → ADMIN_CHANNEL API

    ADMIN_BOT_ID: int = 0
    WORKFLOW_ID: str = ""

    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""

    BZB_API_KEY: str = ""
    BZB_API_URL: str = ""

    DB_HOST: str = "localhost"
    DB_PORT: str = ""
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""

    ADMIN_CHANNEL_ID: Optional[str] = None
    PAYMENT_THREAD_ID: int = 0
    SUPPORT_THREAD_ID: int = 0

    CLUB_GROUP_ID: int = 0
    CLUB_POST_LINK: str = ""
    CLUB_INVITE_TTL_HOURS: int = 24
    CLUB_GROUP_AUDIT_HOUR_UTC: int = 21
    WELCOME_TOPIC_ID: int = 0
    REACT_TOPIC_ID: int = 0
    GIFT_LINK_VALIDITY_DAYS: int = 30
    PUBLIC_OFFER_PDF_FILE_ID: Optional[str] = None
    TELEGRAM_BOT_USERNAME: Optional[str] = None

    LOG_LEVEL: str = "INFO"
    MAX_WORKERS: int = 5

    MEDIA_INBOUND_ARCHIVE_DIR: str = "data/media_inbound"

    # Фоновый опрос статусов YooKassa/BZB (PaymentChecker). По умолчанию выключен.
    PAYMENT_CHECKER_ENABLED: bool = False

    # Telegram user_id суперадмина: полный доступ без лицензии; единственный, кто /admin_add и /admin_block.
    SUPER_ADMIN_ID: int = 0
    # Личка: доступ только у супера и bot_admins; остальным — только /start и сообщение об ожидании.
    BOT_ACCESS_ADMIN_ONLY: bool = True

    # RAG (изолированный пакет ``rag/``; см. ``bot/integrations/rag_bridge.py``)
    RAG_ENABLED: bool = True
    CHROMA_PERSIST_DIR: str = "chroma_data"
    RAG_EXPERT_COLLECTION: str = "expert_materials"
    RAG_GOLDEN_COLLECTION: str = "golden_examples"
    RAG_EMBEDDING_MODEL: str = "text-embedding-3-small"
    RAG_CHUNK_SIZE_TOKENS: int = 600
    RAG_CHUNK_OVERLAP_TOKENS: int = 100
    RAG_TIKTOKEN_ENCODING: str = "cl100k_base"

    # Группа с топиками для автоматического наполнения RAG (0 = выключено).
    # Legacy: одна группа. Если задан RAG_GROUPS — этот параметр игнорируется.
    RAG_GROUP_CHAT_ID: int = 0
    RAG_MIN_INDEX_CHARS: int = 300
    RAG_TAG_MODEL: str = "gpt-4o-mini"
    # Подробные логи индекса RAG из группы (см. main._setup_logging + RAG_INDEXER_DEBUG в .env).
    RAG_INDEXER_DEBUG: bool = False
    # Реплаи в RAG-группу после индексации (✓ и ошибки); False = тихий режим, в группу не писать.
    RAG_GROUP_INDEX_REPLIES: bool = True
    # Legacy: список message_thread_id топиков группы RAG_GROUP_CHAT_ID через запятую; пусто = все.
    RAG_GROUP_TOPIC_IDS: str = ""
    # Несколько групп: «chat_id:topic1,topic2;chat_id2;chat_id3:topic5».
    # Группы через «;», топики через «,» после «:». Без топиков = все.
    # Приоритет над RAG_GROUP_CHAT_ID + RAG_GROUP_TOPIC_IDS.
    RAG_GROUPS: str = ""
    # Топики (message_thread_id), которые никогда не индексировать, во всех RAG-группах.
    RAG_EXCLUDE_TOPIC_IDS: str = ""
    # Группы/топики с отзывами клиентов (формат как RAG_GROUPS). Индексируются в ту же Chroma.
    RAG_TESTIMONIAL_GROUPS: str = ""
    # Сколько последних реплик пользователя отдавать планировщику RAG и Chroma.
    RAG_RETRIEVAL_CONTEXT_USER_TURNS: int = 4
    # Макс. чанков отзывов в промпте (если планировщик включил testimonials).
    RAG_TESTIMONIAL_MAX_CHUNKS: int = 2

    # /new: сколько последних реплик задачи подставлять в DeepSeek (creative_task_turns)
    CREATIVE_TASK_HISTORY_MAX_MESSAGES: int = 24

    # Отдельный сервис MTProto (t.me/...): скачивание файлов > лимита Bot API (~20 МБ).
    TG_MTPRO_DOWNLOADER_URL: str = ""
    TG_MTPRO_DOWNLOADER_API_KEY: str = ""

    # Яндекс.Диск → RAG (WebDAV, фоновый импорт аудио).
    YANDEX_DISK_ENABLED: bool = False
    YANDEX_DISK_LOGIN: str = ""
    YANDEX_DISK_PASSWORD: str = ""
    # JSON: {"sources":[{id, path, masks, hint, ...}]} или путь к файлу ниже.
    YANDEX_DISK_SOURCES: str = ""
    YANDEX_DISK_SOURCES_FILE: str = "config/yandex_disk_sources.json"
    YANDEX_DISK_POLL_INTERVAL_SEC: int = 3600
    YANDEX_DISK_TRANSCRIPT_HEAD_CHARS: int = 2000

    # Телемост → почта Яндекса → подтверждение в группе → RAG.
    TELEMOST_MAIL_ENABLED: bool = False
    TELEMOST_MAIL_IMAP_HOST: str = "imap.yandex.ru"
    TELEMOST_MAIL_IMAP_PORT: int = 993
    TELEMOST_MAIL_LOGIN: str = ""
    TELEMOST_MAIL_PASSWORD: str = ""
    TELEMOST_MAIL_FOLDER: str = "INBOX"
    TELEMOST_MAIL_FROM_MARKERS: str = "keeper@telemost.yandex.ru"
    TELEMOST_MAIL_POLL_INTERVAL_SEC: int = 300
    TELEMOST_MAIL_CLUB_HINT: str = ""
    TELEMOST_MAIL_AVATAR_SPEAKER_NAMES: str = ""
    TELEMOST_MAIL_DEFAULT_PRODUCT: str = ""

    # Шортсы из записей Телемоста (после «Загрузить в RAG»).
    TELEMOST_SHORTS_ENABLED: bool = False
    TELEMOST_VIDEO_SHORTS_ENABLED: bool = False
    TELEMOST_SHORTS_COUNT: int = 5
    TELEMOST_SHORTS_MAX_DURATION_SEC: int = 60
    TELEMOST_SHORTS_USE_ARENA: bool = False
    TELEMOST_SHORTS_VIDEO_DIR: str = "data/telemost_video"
    TELEMOST_SHORTS_WORK_DIR: str = "data/telemost_shorts"
    TELEMOST_SHORTS_PHILOSOPHY_HINT: str = ""
    TELEMOST_SHORTS_WAIT_RECORDING_SEC: int = 7200
    TELEMOST_SHORTS_POLL_INTERVAL_SEC: int = 120
    TELEMOST_RECORDINGS_WEBDAV_DIR: str = "/Записи Телемоста"
    TELEMOST_SHORTS_SUBTITLE_OFFSET_SEC: float = -2.5

    TELEMOST_AUDIO_CLIPS_ENABLED: bool = False
    TELEMOST_AUDIO_CLIPS_COUNT: int = 5
    TELEMOST_AUDIO_CLIPS_MAX_DURATION_SEC: int = 60
    TELEMOST_AUDIO_CLIPS_OFFSET_SEC: float = -2.5
    TELEMOST_AUDIO_DIR: str = "data/telemost_audio"
    TELEMOST_AUDIO_WORK_DIR: str = "data/telemost_audio_clips"

    RAG_SHORTS_CHAT_ID: int = 0
    RAG_SHORTS_TOPIC_ID: int = 582

    # Админская группа RAG: уведомления, догрузка, публичность источников (формат: -1003756916561 / 3756916561).
    RAG_ADMIN_CHAT_ID: int = 0
    RAG_ADMIN_TOPIC_ID: int = 0

    # Устаревшие алиасы (если RAG_ADMIN_* пусты).
    TELEMOST_MAIL_NOTIFY_CHAT_ID: int = 0
    TELEMOST_MAIL_NOTIFY_TOPIC_ID: int = 0
    RAG_SOURCE_VISIBILITY_CHAT_ID: int = 0
    RAG_SOURCE_VISIBILITY_TOPIC_ID: int = 0

    # Контент-аватар (курс / редакция)
    COURSE_ENABLED: bool = True
    ACTIVE_PRODUCT: str = "mbt"
    PRODUCTS_FILE: str = "config/products.json"
    EXPERT_NAME: str = ""
    COURSE_DISK_ROOT: str = "/Аватар"
    COURSE_DISK_POLL_SEC: int = 900
    VIDEO_HOST_PRIORITY: str = "youtube,vimeo,kinescope,disk"
    COURSE_TRANSCRIBE_MODE: str = "auto"
    COURSE_SUBS_MIN_CHARS_PER_MIN: int = 300
    YTDLP_COOKIES_FILE: str = ""
    COURSE_PLAYLIST_MAX: int = 100
    COURSE_MEDIA_MAX_GB: float = 4.0
    COURSE_TMP_DIR: str = "data/tmp"
    KINESCOPE_API_TOKEN: str = ""
    VIMEO_ACCESS_TOKEN: str = ""
    COURSE_VISION_MODEL: str = "gpt-4o-mini"
    COURSE_VISION_MAX_PAGES: int = 80
    COURSE_MINING_MODEL: str = "gpt-4o-mini"
    COURSE_PLANNER_MODEL: str = "gpt-4o-mini"
    CONTENT_WRITER_MODEL: str = "deepseek-v4-flash"
    STYLE_MODEL: str = "gpt-4o"
    COURSE_LLM_CONCURRENCY: int = 3
    COURSE_CARDS_PER_HOUR: int = 30
    COURSE_CARD_DUP_SIM: float = 0.88
    COURSE_PASSPORT_DEBOUNCE_SEC: int = 600
    COURSE_INFO_MAX_CHARS: int = 8000
    COURSE_USE_LEGACY_CHUNKS: bool = True
    CONTENT_PLAN_WEEKDAY: int = 0
    CONTENT_PLAN_HOUR: int = 9
    CONTENT_PLAN_TZ: str = "Europe/Moscow"
    CONTENT_PLAN_MIX: str = "tg_post:3,reels:2,carousel:1,stories:1"
    CARD_REUSE_DAYS: int = 60
    COST_TABLE: str = ""
    RAG_CARDS_COLLECTION: str = "content_cards"

    @property
    def video_host_priority_list(self) -> tuple[str, ...]:
        parts = [p.strip().lower() for p in (self.VIDEO_HOST_PRIORITY or "").split(",")]
        return tuple(p for p in parts if p)

    @property
    def content_plan_mix_map(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for part in (self.CONTENT_PLAN_MIX or "").split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = max(0, int(v.strip(), 10))
            except ValueError:
                continue
        return out

    @property
    def cost_table_map(self) -> Dict[str, Any]:
        raw = (self.COST_TABLE or "").strip()
        if not raw:
            return {
                "whisper_per_min": 0.006,
                "models": {
                    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
                    "gpt-4o": {"in": 2.50, "out": 10.00},
                    "gpt-4.1-mini": {"in": 0.40, "out": 1.60},
                    "deepseek-v4-flash": {"in": 0.14, "out": 0.28},
                },
            }
        try:
            import json as _json

            data = _json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @property
    def rag_shorts_chat_id(self) -> int:
        raw = int(self.RAG_SHORTS_CHAT_ID or 0)
        if raw:
            return _normalize_supergroup_chat_id(raw)
        return self.rag_admin_chat_id

    @property
    def rag_admin_chat_id(self) -> int:
        raw = int(self.RAG_ADMIN_CHAT_ID or 0)
        if raw:
            return _normalize_supergroup_chat_id(raw)
        for alt in (
            self.RAG_SOURCE_VISIBILITY_CHAT_ID,
            self.TELEMOST_MAIL_NOTIFY_CHAT_ID,
        ):
            a = int(alt or 0)
            if a:
                return _normalize_supergroup_chat_id(a)
        return 0

    @property
    def rag_admin_topic_id(self) -> int:
        for val in (
            self.RAG_ADMIN_TOPIC_ID,
            self.RAG_SOURCE_VISIBILITY_TOPIC_ID,
            self.TELEMOST_MAIL_NOTIFY_TOPIC_ID,
        ):
            tid = int(val or 0)
            if tid:
                return tid
        return 0

    @property
    def rag_indexer_verbose(self) -> bool:
        """Широкий лог RAG-индексера: флаг или общий LOG_LEVEL=DEBUG."""
        if self.RAG_INDEXER_DEBUG:
            return True
        return str(self.LOG_LEVEL or "").strip().upper() == "DEBUG"

    @property
    def rag_exclude_topic_ids(self) -> FrozenSet[int]:
        """Глобальный denylist топиков (админские ветки и т.п.)."""
        return _parse_rag_group_topic_allowlist(self.RAG_EXCLUDE_TOPIC_IDS) or frozenset()

    @property
    def rag_group_topic_allowlist(self) -> Optional[frozenset[int]]:
        """None = все топики; иначе только перечисленные message_thread_id.

        Legacy-свойство для одной группы. Для нескольких — ``rag_groups_map``.
        """
        return _parse_rag_group_topic_allowlist(self.RAG_GROUP_TOPIC_IDS)

    @property
    def rag_groups_map(self) -> Dict[int, Optional[frozenset[int]]]:
        """Все RAG-группы: {chat_id: frozenset(topic_ids) | None}.

        None в значении = все топики группы.
        Если RAG_GROUPS задан — берём оттуда; иначе fallback на RAG_GROUP_CHAT_ID.
        """
        return _parse_rag_groups(
            self.RAG_GROUPS,
            fallback_chat_id=self.RAG_GROUP_CHAT_ID,
            fallback_topic_ids=self.RAG_GROUP_TOPIC_IDS,
        )

    @property
    def rag_testimonial_groups_map(self) -> Dict[int, Optional[frozenset[int]]]:
        """Группы с отзывами клиентов: {chat_id: frozenset(topic_ids) | None}."""
        return _parse_rag_groups(self.RAG_TESTIMONIAL_GROUPS)

    @property
    def rag_index_groups_map(self) -> Dict[int, Optional[frozenset[int]]]:
        """Объединение expert + testimonial групп для регистрации хендлера индексации."""
        merged: Dict[int, Optional[frozenset[int]]] = {}
        for src in (self.rag_groups_map, self.rag_testimonial_groups_map):
            for gid, topics in src.items():
                if gid not in merged:
                    merged[gid] = topics
                    continue
                prev = merged[gid]
                if prev is None or topics is None:
                    merged[gid] = None
                else:
                    merged[gid] = prev | topics
        return merged

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def telegram_token_for_admin_channel(self) -> Optional[str]:
        """
        Токен Bot API для createForumTopic/sendMessage в ADMIN_CHANNEL: тот же,
        что основной бот (BIBLIA_BOT_TOKEN). Иначе — legacy ADMIN_BOT_TOKEN.
        """
        main = (self.BIBLIA_BOT_TOKEN or "").strip()
        if main:
            return main
        return (self.ADMIN_BOT_TOKEN or "").strip() or None

    @property
    def has_admin_bot(self) -> bool:
        return bool(self.telegram_token_for_admin_channel)

    @property
    def has_admin_channel(self) -> bool:
        return bool(self.ADMIN_CHANNEL_ID)

    @property
    def has_yookassa(self) -> bool:
        return bool(self.YOOKASSA_SHOP_ID and self.YOOKASSA_SECRET_KEY)

    @property
    def media_inbound_archive_enabled(self) -> bool:
        v = str(self.MEDIA_INBOUND_ARCHIVE_DIR or "").strip().lower()
        if not v or v in ("0", "false", "no", "off"):
            return False
        return True

    @property
    def resolved_media_inbound_archive_root(self) -> Path:
        raw = Path(self.MEDIA_INBOUND_ARCHIVE_DIR.strip())
        if raw.is_absolute():
            return raw
        return Path(__file__).resolve().parent / raw

    @property
    def resolved_chroma_persist_dir(self) -> Path:
        """Каталог Chroma относительно каталога проекта (рядом с config.py), если путь не абсолютный."""
        raw = Path(str(self.CHROMA_PERSIST_DIR or "").strip() or "chroma_data")
        if raw.is_absolute():
            return raw
        return Path(__file__).resolve().parent / raw


def _app_db_name() -> str:
    """DB_NAME в .env или fallback на BIBLIA_DB_NAME — та же БД, что и у BibliaBotConfig."""
    a = (os.getenv("DB_NAME") or "").strip()
    if a:
        return a
    return (os.getenv("BIBLIA_DB_NAME") or "").strip()


def _safe_int_env(key: str, default: int, *, min_v: int = 0, max_v: int = 10_000) -> int:
    try:
        n = int((os.getenv(key) or str(default)).strip(), 10)
    except ValueError:
        return default
    return max(min_v, min(max_v, n))


def _normalize_supergroup_chat_id(raw: int) -> int:
    n = int(raw or 0)
    if n == 0:
        return 0
    if n > 0:
        s = str(n)
        if not s.startswith("100") and len(s) >= 9:
            return int(f"-100{n}")
    return n


def _parse_super_admin_id(raw: Optional[str]) -> int:
    if raw is None or not str(raw).strip():
        return 0
    try:
        return int(str(raw).strip(), 10)
    except ValueError:
        return 0


def _parse_rag_group_topic_allowlist(raw: Optional[str]) -> Optional[FrozenSet[int]]:
    s = (raw or "").strip()
    if not s:
        return None
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part, 10))
        except ValueError:
            logging.getLogger(__name__).warning(
                "RAG_GROUP_TOPIC_IDS: пропуск нечислового фрагмента %r", part
            )
    if not out:
        return None
    return frozenset(out)


def _parse_rag_groups(
    raw_groups: Optional[str],
    *,
    fallback_chat_id: int = 0,
    fallback_topic_ids: str = "",
) -> Dict[int, Optional[FrozenSet[int]]]:
    """Парсинг ``RAG_GROUPS`` → ``{chat_id: frozenset | None}``.

    Формат: ``chat_id:topic1,topic2;chat_id2;chat_id3:topic5``
    - Группы через ``;``
    - Топики через ``,`` после ``:``; без топиков = все
    - Если ``raw_groups`` пуст — fallback на ``RAG_GROUP_CHAT_ID`` + ``RAG_GROUP_TOPIC_IDS``
    """
    _log = logging.getLogger(__name__)
    result: Dict[int, Optional[FrozenSet[int]]] = {}

    s = (raw_groups or "").strip()
    if s:
        for entry in s.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                gid_raw, topics_raw = entry.split(":", 1)
            else:
                gid_raw, topics_raw = entry, ""
            try:
                gid = int(gid_raw.strip(), 10)
            except ValueError:
                _log.warning("RAG_GROUPS: пропуск нечислового chat_id %r", gid_raw)
                continue
            if not gid:
                continue
            allow = _parse_rag_group_topic_allowlist(topics_raw.strip())
            result[gid] = allow
        return result

    gid = int(fallback_chat_id or 0)
    if gid:
        result[gid] = _parse_rag_group_topic_allowlist(fallback_topic_ids)
    return result


def _env_flag_true(key: str, default: bool = False) -> bool:
    v = (os.getenv(key) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def load_app_config() -> AppConfig:
    media_raw = os.getenv("MEDIA_INBOUND_ARCHIVE_DIR", "data/media_inbound")
    return AppConfig(
        MIRON_BOT_TOKEN=os.getenv("MIRON_BOT_TOKEN", ""),
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        WORKFLOW_ID=os.getenv("WORKFLOW_ID", ""),
        YOOKASSA_SHOP_ID=os.getenv("YOOKASSA_SHOP_ID", ""),
        YOOKASSA_SECRET_KEY=os.getenv("YOOKASSA_SECRET_KEY", ""),
        BZB_API_KEY=os.getenv("BZB_API_KEY", ""),
        BZB_API_URL=os.getenv("BZB_API_URL", ""),
        ADMIN_BOT_TOKEN=os.getenv("ADMIN_BOT_TOKEN"),
        BIBLIA_BOT_TOKEN=(os.getenv("BIBLIA_BOT_TOKEN") or "").strip() or None,
        DB_HOST=os.getenv("DB_HOST", "localhost"),
        DB_PORT=os.getenv("DB_PORT", ""),
        DB_NAME=_app_db_name(),
        DB_USER=os.getenv("DB_USER", ""),
        DB_PASSWORD=os.getenv("DB_PASSWORD", ""),
        ADMIN_CHANNEL_ID=os.getenv("ADMIN_CHANNEL_ID"),
        PAYMENT_THREAD_ID=int(os.getenv("PAYMENT_THREAD_ID", "0")),
        SUPPORT_THREAD_ID=int(os.getenv("SUPPORT_THREAD_ID", "0")),
        ADMIN_BOT_ID=int(os.getenv("ADMIN_BOT_ID", "0")),
        CLUB_GROUP_ID=int(os.getenv("CLUB_GROUP_ID", "0")),
        CLUB_POST_LINK=(os.getenv("CLUB_POST_LINK") or "").strip(),
        CLUB_INVITE_TTL_HOURS=int(os.getenv("CLUB_INVITE_TTL_HOURS", "24")),
        CLUB_GROUP_AUDIT_HOUR_UTC=int(os.getenv("CLUB_GROUP_AUDIT_HOUR_UTC", "21")) % 24,
        WELCOME_TOPIC_ID=int(os.getenv("WELCOME_TOPIC_ID", "0")),
        REACT_TOPIC_ID=int(os.getenv("REACT_TOPIC_ID", "0")),
        GIFT_LINK_VALIDITY_DAYS=_parse_gift_link_validity_days(os.getenv("GIFT_LINK_VALIDITY_DAYS")),
        PUBLIC_OFFER_PDF_FILE_ID=((os.getenv("PUBLIC_OFFER_PDF_FILE_ID") or "").strip() or None),
        TELEGRAM_BOT_USERNAME=_normalize_env_username(os.getenv("TELEGRAM_BOT_USERNAME", "")),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        MEDIA_INBOUND_ARCHIVE_DIR=media_raw,
        PAYMENT_CHECKER_ENABLED=_env_flag_true("PAYMENT_CHECKER_ENABLED", default=False),
        SUPER_ADMIN_ID=_parse_super_admin_id(os.getenv("SUPER_ADMIN_ID")),
        BOT_ACCESS_ADMIN_ONLY=_env_flag_true("BOT_ACCESS_ADMIN_ONLY", default=True),
        RAG_ENABLED=_env_flag_true("RAG_ENABLED", default=True),
        CHROMA_PERSIST_DIR=os.getenv("CHROMA_PERSIST_DIR", "chroma_data").strip()
        or "chroma_data",
        RAG_EXPERT_COLLECTION=os.getenv("RAG_EXPERT_COLLECTION", "expert_materials").strip()
        or "expert_materials",
        RAG_GOLDEN_COLLECTION=os.getenv("RAG_GOLDEN_COLLECTION", "golden_examples").strip()
        or "golden_examples",
        RAG_EMBEDDING_MODEL=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small").strip()
        or "text-embedding-3-small",
        RAG_CHUNK_SIZE_TOKENS=_safe_int_env(
            "RAG_CHUNK_SIZE_TOKENS", 600, min_v=100, max_v=4000
        ),
        RAG_CHUNK_OVERLAP_TOKENS=_safe_int_env(
            "RAG_CHUNK_OVERLAP_TOKENS", 100, min_v=0, max_v=2000
        ),
        RAG_TIKTOKEN_ENCODING=os.getenv("RAG_TIKTOKEN_ENCODING", "cl100k_base").strip()
        or "cl100k_base",
        RAG_GROUP_CHAT_ID=int(os.getenv("RAG_GROUP_CHAT_ID", "0") or 0),
        RAG_MIN_INDEX_CHARS=_safe_int_env(
            "RAG_MIN_INDEX_CHARS", 300, min_v=1, max_v=50_000
        ),
        RAG_TAG_MODEL=os.getenv("RAG_TAG_MODEL", "gpt-4o-mini").strip()
        or "gpt-4o-mini",
        RAG_INDEXER_DEBUG=_env_flag_true("RAG_INDEXER_DEBUG", default=False),
        RAG_GROUP_INDEX_REPLIES=_env_flag_true("RAG_GROUP_INDEX_REPLIES", default=True),
        RAG_GROUP_TOPIC_IDS=(os.getenv("RAG_GROUP_TOPIC_IDS") or "").strip(),
        RAG_GROUPS=(os.getenv("RAG_GROUPS") or "").strip(),
        RAG_EXCLUDE_TOPIC_IDS=(os.getenv("RAG_EXCLUDE_TOPIC_IDS") or "").strip(),
        RAG_TESTIMONIAL_GROUPS=(os.getenv("RAG_TESTIMONIAL_GROUPS") or "").strip(),
        RAG_RETRIEVAL_CONTEXT_USER_TURNS=_safe_int_env(
            "RAG_RETRIEVAL_CONTEXT_USER_TURNS", 4, min_v=1, max_v=12
        ),
        RAG_TESTIMONIAL_MAX_CHUNKS=_safe_int_env(
            "RAG_TESTIMONIAL_MAX_CHUNKS", 2, min_v=0, max_v=6
        ),
        CREATIVE_TASK_HISTORY_MAX_MESSAGES=_safe_int_env(
            "CREATIVE_TASK_HISTORY_MAX_MESSAGES", 24, min_v=4, max_v=64
        ),
        TG_MTPRO_DOWNLOADER_URL=(os.getenv("TG_MTPRO_DOWNLOADER_URL") or "").strip(),
        TG_MTPRO_DOWNLOADER_API_KEY=(os.getenv("TG_MTPRO_DOWNLOADER_API_KEY") or "").strip(),
        YANDEX_DISK_ENABLED=_env_flag_true("YANDEX_DISK_ENABLED", default=False),
        YANDEX_DISK_LOGIN=(os.getenv("YANDEX_DISK_LOGIN") or "").strip(),
        YANDEX_DISK_PASSWORD=(os.getenv("YANDEX_DISK_PASSWORD") or "").strip(),
        YANDEX_DISK_SOURCES=(os.getenv("YANDEX_DISK_SOURCES") or "").strip(),
        YANDEX_DISK_SOURCES_FILE=(
            os.getenv("YANDEX_DISK_SOURCES_FILE") or "config/yandex_disk_sources.json"
        ).strip(),
        YANDEX_DISK_POLL_INTERVAL_SEC=_safe_int_env(
            "YANDEX_DISK_POLL_INTERVAL_SEC", 3600, min_v=300, max_v=86400
        ),
        YANDEX_DISK_TRANSCRIPT_HEAD_CHARS=_safe_int_env(
            "YANDEX_DISK_TRANSCRIPT_HEAD_CHARS", 2000, min_v=200, max_v=8000
        ),
        TELEMOST_MAIL_ENABLED=_env_flag_true("TELEMOST_MAIL_ENABLED", default=False),
        TELEMOST_MAIL_IMAP_HOST=(
            os.getenv("TELEMOST_MAIL_IMAP_HOST") or "imap.yandex.ru"
        ).strip(),
        TELEMOST_MAIL_IMAP_PORT=_safe_int_env(
            "TELEMOST_MAIL_IMAP_PORT", 993, min_v=1, max_v=65535
        ),
        TELEMOST_MAIL_LOGIN=(os.getenv("TELEMOST_MAIL_LOGIN") or "").strip(),
        TELEMOST_MAIL_PASSWORD=(os.getenv("TELEMOST_MAIL_PASSWORD") or "").strip(),
        TELEMOST_MAIL_FOLDER=(os.getenv("TELEMOST_MAIL_FOLDER") or "INBOX").strip(),
        TELEMOST_MAIL_FROM_MARKERS=(
            os.getenv("TELEMOST_MAIL_FROM_MARKERS") or "keeper@telemost.yandex.ru"
        ).strip(),
        TELEMOST_MAIL_POLL_INTERVAL_SEC=_safe_int_env(
            "TELEMOST_MAIL_POLL_INTERVAL_SEC", 300, min_v=60, max_v=86400
        ),
        RAG_ADMIN_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("RAG_ADMIN_CHAT_ID", "0") or 0)
        ),
        RAG_ADMIN_TOPIC_ID=int(os.getenv("RAG_ADMIN_TOPIC_ID", "0") or 0),
        TELEMOST_MAIL_NOTIFY_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("TELEMOST_MAIL_NOTIFY_CHAT_ID", "0") or 0)
        ),
        TELEMOST_MAIL_NOTIFY_TOPIC_ID=int(
            os.getenv("TELEMOST_MAIL_NOTIFY_TOPIC_ID", "0") or 0
        ),
        TELEMOST_MAIL_CLUB_HINT=(os.getenv("TELEMOST_MAIL_CLUB_HINT") or "").strip(),
        TELEMOST_MAIL_AVATAR_SPEAKER_NAMES=(
            os.getenv("TELEMOST_MAIL_AVATAR_SPEAKER_NAMES") or ""
        ).strip(),
        TELEMOST_MAIL_DEFAULT_PRODUCT=(
            os.getenv("TELEMOST_MAIL_DEFAULT_PRODUCT") or ""
        ).strip(),
        TELEMOST_SHORTS_ENABLED=_env_flag_true("TELEMOST_SHORTS_ENABLED", default=False),
        TELEMOST_VIDEO_SHORTS_ENABLED=_env_flag_true(
            "TELEMOST_VIDEO_SHORTS_ENABLED", default=False
        ),
        TELEMOST_SHORTS_COUNT=_safe_int_env(
            "TELEMOST_SHORTS_COUNT", 5, min_v=1, max_v=10
        ),
        TELEMOST_SHORTS_MAX_DURATION_SEC=_safe_int_env(
            "TELEMOST_SHORTS_MAX_DURATION_SEC", 60, min_v=15, max_v=60
        ),
        TELEMOST_SHORTS_USE_ARENA=_env_flag_true(
            "TELEMOST_SHORTS_USE_ARENA", default=False
        ),
        TELEMOST_SHORTS_VIDEO_DIR=(
            os.getenv("TELEMOST_SHORTS_VIDEO_DIR") or "data/telemost_video"
        ).strip(),
        TELEMOST_SHORTS_WORK_DIR=(
            os.getenv("TELEMOST_SHORTS_WORK_DIR") or "data/telemost_shorts"
        ).strip(),
        TELEMOST_SHORTS_PHILOSOPHY_HINT=(
            os.getenv("TELEMOST_SHORTS_PHILOSOPHY_HINT") or ""
        ).strip(),
        TELEMOST_SHORTS_WAIT_RECORDING_SEC=_safe_int_env(
            "TELEMOST_SHORTS_WAIT_RECORDING_SEC", 7200, min_v=300, max_v=86_400
        ),
        TELEMOST_SHORTS_POLL_INTERVAL_SEC=_safe_int_env(
            "TELEMOST_SHORTS_POLL_INTERVAL_SEC", 120, min_v=30, max_v=600
        ),
        TELEMOST_RECORDINGS_WEBDAV_DIR=(
            os.getenv("TELEMOST_RECORDINGS_WEBDAV_DIR") or "/Записи Телемоста"
        ).strip(),
        TELEMOST_SHORTS_SUBTITLE_OFFSET_SEC=float(
            os.getenv("TELEMOST_SHORTS_SUBTITLE_OFFSET_SEC", "-2.5") or -2.5
        ),
        TELEMOST_AUDIO_CLIPS_ENABLED=_env_flag_true(
            "TELEMOST_AUDIO_CLIPS_ENABLED", default=False
        ),
        TELEMOST_AUDIO_CLIPS_COUNT=_safe_int_env(
            "TELEMOST_AUDIO_CLIPS_COUNT", 5, min_v=1, max_v=10
        ),
        TELEMOST_AUDIO_CLIPS_MAX_DURATION_SEC=_safe_int_env(
            "TELEMOST_AUDIO_CLIPS_MAX_DURATION_SEC", 60, min_v=45, max_v=60
        ),
        TELEMOST_AUDIO_CLIPS_OFFSET_SEC=float(
            os.getenv("TELEMOST_AUDIO_CLIPS_OFFSET_SEC", "-2.5") or -2.5
        ),
        TELEMOST_AUDIO_DIR=(
            os.getenv("TELEMOST_AUDIO_DIR") or "data/telemost_audio"
        ).strip(),
        TELEMOST_AUDIO_WORK_DIR=(
            os.getenv("TELEMOST_AUDIO_WORK_DIR") or "data/telemost_audio_clips"
        ).strip(),
        RAG_SHORTS_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("RAG_SHORTS_CHAT_ID", "0") or 0)
        ),
        RAG_SHORTS_TOPIC_ID=int(os.getenv("RAG_SHORTS_TOPIC_ID", "582") or 582),
        RAG_SOURCE_VISIBILITY_CHAT_ID=_normalize_supergroup_chat_id(
            int(os.getenv("RAG_SOURCE_VISIBILITY_CHAT_ID", "0") or 0)
        ),
        RAG_SOURCE_VISIBILITY_TOPIC_ID=int(
            os.getenv("RAG_SOURCE_VISIBILITY_TOPIC_ID", "0") or 0
        ),
        COURSE_ENABLED=_env_flag_true("COURSE_ENABLED", default=True),
        ACTIVE_PRODUCT=(os.getenv("ACTIVE_PRODUCT") or "mbt").strip() or "mbt",
        PRODUCTS_FILE=(os.getenv("PRODUCTS_FILE") or "config/products.json").strip(),
        EXPERT_NAME=(os.getenv("EXPERT_NAME") or "").strip(),
        COURSE_DISK_ROOT=(os.getenv("COURSE_DISK_ROOT") or "/Аватар").strip() or "/Аватар",
        COURSE_DISK_POLL_SEC=_safe_int_env(
            "COURSE_DISK_POLL_SEC", 900, min_v=60, max_v=86400
        ),
        VIDEO_HOST_PRIORITY=(
            os.getenv("VIDEO_HOST_PRIORITY") or "youtube,vimeo,kinescope,disk"
        ).strip(),
        COURSE_TRANSCRIBE_MODE=(os.getenv("COURSE_TRANSCRIBE_MODE") or "auto").strip()
        or "auto",
        COURSE_SUBS_MIN_CHARS_PER_MIN=_safe_int_env(
            "COURSE_SUBS_MIN_CHARS_PER_MIN", 300, min_v=0, max_v=5000
        ),
        YTDLP_COOKIES_FILE=(os.getenv("YTDLP_COOKIES_FILE") or "").strip(),
        COURSE_PLAYLIST_MAX=_safe_int_env(
            "COURSE_PLAYLIST_MAX", 100, min_v=1, max_v=500
        ),
        COURSE_MEDIA_MAX_GB=float(os.getenv("COURSE_MEDIA_MAX_GB", "4") or 4),
        COURSE_TMP_DIR=(os.getenv("COURSE_TMP_DIR") or "data/tmp").strip() or "data/tmp",
        KINESCOPE_API_TOKEN=(os.getenv("KINESCOPE_API_TOKEN") or "").strip(),
        VIMEO_ACCESS_TOKEN=(os.getenv("VIMEO_ACCESS_TOKEN") or "").strip(),
        COURSE_VISION_MODEL=(os.getenv("COURSE_VISION_MODEL") or "gpt-4o-mini").strip()
        or "gpt-4o-mini",
        COURSE_VISION_MAX_PAGES=_safe_int_env(
            "COURSE_VISION_MAX_PAGES", 80, min_v=1, max_v=400
        ),
        COURSE_MINING_MODEL=(os.getenv("COURSE_MINING_MODEL") or "gpt-4o-mini").strip()
        or "gpt-4o-mini",
        COURSE_PLANNER_MODEL=(
            os.getenv("COURSE_PLANNER_MODEL") or os.getenv("RAG_TAG_MODEL") or "gpt-4o-mini"
        ).strip()
        or "gpt-4o-mini",
        CONTENT_WRITER_MODEL=(
            os.getenv("CONTENT_WRITER_MODEL") or "deepseek-v4-flash"
        ).strip()
        or "deepseek-v4-flash",
        STYLE_MODEL=(os.getenv("STYLE_MODEL") or "gpt-4o").strip() or "gpt-4o",
        COURSE_LLM_CONCURRENCY=_safe_int_env(
            "COURSE_LLM_CONCURRENCY", 3, min_v=1, max_v=8
        ),
        COURSE_CARDS_PER_HOUR=_safe_int_env(
            "COURSE_CARDS_PER_HOUR", 30, min_v=1, max_v=200
        ),
        COURSE_CARD_DUP_SIM=float(os.getenv("COURSE_CARD_DUP_SIM", "0.88") or 0.88),
        COURSE_PASSPORT_DEBOUNCE_SEC=_safe_int_env(
            "COURSE_PASSPORT_DEBOUNCE_SEC", 600, min_v=30, max_v=7200
        ),
        COURSE_INFO_MAX_CHARS=_safe_int_env(
            "COURSE_INFO_MAX_CHARS", 8000, min_v=500, max_v=50_000
        ),
        COURSE_USE_LEGACY_CHUNKS=_env_flag_true("COURSE_USE_LEGACY_CHUNKS", default=True),
        CONTENT_PLAN_WEEKDAY=_safe_int_env("CONTENT_PLAN_WEEKDAY", 0, min_v=0, max_v=6),
        CONTENT_PLAN_HOUR=_safe_int_env("CONTENT_PLAN_HOUR", 9, min_v=0, max_v=23),
        CONTENT_PLAN_TZ=(os.getenv("CONTENT_PLAN_TZ") or "Europe/Moscow").strip()
        or "Europe/Moscow",
        CONTENT_PLAN_MIX=(
            os.getenv("CONTENT_PLAN_MIX") or "tg_post:3,reels:2,carousel:1,stories:1"
        ).strip(),
        CARD_REUSE_DAYS=_safe_int_env("CARD_REUSE_DAYS", 60, min_v=1, max_v=365),
        COST_TABLE=(os.getenv("COST_TABLE") or "").strip(),
        RAG_CARDS_COLLECTION=(os.getenv("RAG_CARDS_COLLECTION") or "content_cards").strip()
        or "content_cards",
    )


config = load_app_config()


def russian_days_phrase(days: int) -> str:
    n = int(days)
    if n <= 0:
        n = 1
    d100 = n % 100
    if 11 <= d100 <= 14:
        return f"{n} дней"
    d10 = n % 10
    if d10 == 1:
        return f"{n} день"
    if 2 <= d10 <= 4:
        return f"{n} дня"
    return f"{n} дней"
