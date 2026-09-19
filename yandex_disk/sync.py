"""Синхронизация файлов с Яндекс.Диска → транскрипция → RAG."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from storage.db.rag_import_cache import (
    IMPORT_YANDEX_DISK,
    STATUS_ERROR,
    STATUS_INDEXED,
)
from telemost_mail.backfill_stats import BackfillStats
from yandex_disk.cache_keys import yandex_disk_cache_key

from yandex_disk.metadata_llm import extract_disk_material_metadata
from yandex_disk.patterns import file_matches_masks
from yandex_disk.sources import YandexDiskSource, load_yandex_disk_sources
from yandex_disk.webdav import RemoteFile, YandexDiskWebDAV

if TYPE_CHECKING:
    from openai_client.assistant import OpenAIClient
    from rag.material_index import MaterialIndexService

logger = logging.getLogger(__name__)

_AUDIO_EXT = frozenset({".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac", ".wma"})


@dataclass
class SyncResult:
    source_id: str
    scanned: int = 0
    matched: int = 0
    indexed: int = 0
    skipped: int = 0
    errors: int = 0
    messages: List[str] = None

    def __post_init__(self) -> None:
        if self.messages is None:
            self.messages = []


class YandexDiskSyncService:
    def __init__(
        self,
        *,
        login: str,
        password: str,
        sources: List[YandexDiskSource],
        user_storage,
        openai_client: Optional["OpenAIClient"],
        material_index: Optional["MaterialIndexService"],
        system_user_id: int = 0,
        transcript_head_chars: int = 2000,
        bot_app: Any = None,
    ):
        self._webdav = YandexDiskWebDAV(login, password)
        self._sources = list(sources or [])
        self._storage = user_storage
        self._openai = openai_client
        self._index = material_index
        self._system_user_id = int(system_user_id or 0)
        self._head_chars = max(200, transcript_head_chars)
        self._app = bot_app

    @classmethod
    def from_config(
        cls,
        config,
        *,
        user_storage,
        openai_client,
        material_index,
        bot_app=None,
    ) -> "YandexDiskSyncService":
        root = Path(__file__).resolve().parent.parent
        sources = load_yandex_disk_sources(
            json_inline=getattr(config, "YANDEX_DISK_SOURCES", "") or "",
            json_file=getattr(config, "YANDEX_DISK_SOURCES_FILE", "") or "",
            project_root=root,
        )
        return cls(
            login=getattr(config, "YANDEX_DISK_LOGIN", "") or "",
            password=getattr(config, "YANDEX_DISK_PASSWORD", "") or "",
            sources=sources,
            user_storage=user_storage,
            openai_client=openai_client,
            material_index=material_index,
            system_user_id=int(getattr(config, "SUPER_ADMIN_ID", 0) or 0),
            transcript_head_chars=int(
                getattr(config, "YANDEX_DISK_TRANSCRIPT_HEAD_CHARS", 2000) or 2000
            ),
            bot_app=bot_app,
        )

    @property
    def enabled(self) -> bool:
        return (
            self._webdav.configured
            and bool(self._sources)
            and self._index is not None
            and self._openai is not None
        )

    @property
    def sources_count(self) -> int:
        return len(self._sources)

    async def sync_all(self) -> List[SyncResult]:
        results: List[SyncResult] = []
        if not self.enabled:
            logger.info("Yandex Disk sync: выключен (нет creds/sources/RAG)")
            return results
        for src in self._sources:
            try:
                results.append(await self.sync_source(src))
            except Exception as e:
                logger.exception("yandex_disk sync source %s: %s", src.id, e)
                r = SyncResult(source_id=src.id, errors=1)
                r.messages.append(str(e)[:300])
                results.append(r)
        return results

    async def _list_source_files(
        self, source: YandexDiskSource
    ) -> tuple[str, List[RemoteFile]]:
        """list_files с alternate_paths; возвращает (used_path, files)."""
        paths = [source.path] + [
            p for p in (source.alternate_paths or []) if p and p != source.path
        ]
        files: List[RemoteFile] = []
        used_path = paths[0]
        for p in paths:
            used_path = p
            try:
                files = await self._webdav.list_files(p, recursive=source.recursive)
            except Exception as e:
                err = str(e)
                if "404" in err:
                    logger.warning(
                        "yandex_disk source %s: path missing %s (%s)",
                        source.id,
                        p,
                        err[:160],
                    )
                    files = []
                else:
                    raise
            if files:
                break
            if p != paths[-1]:
                logger.info(
                    "yandex_disk source %s: нет файлов на %s, пробуем alternate",
                    source.id,
                    p,
                )
        if used_path != source.path and files:
            logger.warning(
                "yandex_disk source %s: используем path=%s (основной %s недоступен/пуст)",
                source.id,
                used_path,
                source.path,
            )
        return used_path, files

    async def sync_source(
        self, source: YandexDiskSource, *, since: Optional[datetime] = None
    ) -> SyncResult:
        res = SyncResult(source_id=source.id)
        _used_path, files = await self._list_source_files(source)
        res.scanned = len(files)
        for rf in files:
            if since is not None and not self._file_modified_since(rf, since):
                continue
            if not self._is_audio(rf.name):
                continue
            if not file_matches_masks(rf.name, source.masks):
                continue
            res.matched += 1
            try:
                n = await self._process_file(source, rf)
                if n > 0:
                    res.indexed += 1
                else:
                    res.skipped += 1
            except Exception as e:
                res.errors += 1
                logger.exception("yandex_disk file %s: %s", rf.path, e)
                res.messages.append(f"{rf.name}: {e}"[:200])
        return res

    async def backfill_disk(self, days: int) -> BackfillStats:
        stats = BackfillStats(source="disk", days=max(1, int(days)))
        if not self.enabled:
            stats.messages.append("Синхронизация диска выключена")
            return stats

        since = datetime.now(timezone.utc) - timedelta(days=stats.days)
        for src in self._sources:
            try:
                _used_path, files = await self._list_source_files(src)
                stats.scanned += len(files)
                for rf in files:
                    if not self._file_modified_since(rf, since):
                        continue
                    if not self._is_audio(rf.name):
                        continue
                    if not file_matches_masks(rf.name, src.masks):
                        continue
                    cache_key = yandex_disk_cache_key(src.id, rf.path)
                    if await self._storage.rag_import_cache_should_skip(
                        IMPORT_YANDEX_DISK, cache_key
                    ):
                        stats.skipped_cached += 1
                        continue
                    try:
                        n = await self._process_file(src, rf)
                        if n > 0:
                            stats.indexed += 1
                            stats.chunks += n
                        else:
                            stats.skipped_cached += 1
                    except Exception as e:
                        stats.errors += 1
                        logger.exception("backfill_disk %s: %s", rf.path, e)
                        stats.messages.append(f"{rf.name}: {e}"[:120])
                        await self._storage.rag_import_cache_upsert(
                            import_type=IMPORT_YANDEX_DISK,
                            cache_key=cache_key,
                            status=STATUS_ERROR,
                            label=rf.name[:200],
                            error_message=str(e)[:500],
                        )
            except Exception as e:
                stats.errors += 1
                stats.messages.append(f"{src.id}: {e}"[:200])
        return stats

    def _file_modified_since(
        self, rf: RemoteFile, since: datetime
    ) -> bool:
        mod = rf.modified
        if mod is None:
            return True
        dt: Optional[datetime]
        if isinstance(mod, datetime):
            dt = mod
        else:
            try:
                dt = parsedate_to_datetime(str(mod))
            except Exception:
                return True
        if dt is None:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = since
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        return dt >= cutoff

    def _is_audio(self, name: str) -> bool:
        ext = Path(name).suffix.lower()
        return ext in _AUDIO_EXT

    async def _process_file(self, source: YandexDiskSource, rf: RemoteFile) -> int:
        cache_key = yandex_disk_cache_key(source.id, rf.path)
        if await self._storage.rag_import_cache_should_skip(
            IMPORT_YANDEX_DISK, cache_key
        ):
            return 0

        meta_row = await self._storage.get_yandex_disk_indexed(
            source.id, rf.path
        )
        file_meta = await self._webdav.get_file_meta(rf.path)
        etag = (file_meta.etag if file_meta else "") or rf.etag
        if meta_row and etag and meta_row.get("etag") == etag:
            await self._storage.rag_import_cache_upsert(
                import_type=IMPORT_YANDEX_DISK,
                cache_key=cache_key,
                status=STATUS_INDEXED,
                chunks_count=int(meta_row.get("chunks_count") or 0),
                label=rf.name[:200],
            )
            return 0

        suffix = Path(rf.name).suffix or ".mp3"
        with tempfile.TemporaryDirectory(prefix="ydisk_") as tmp:
            local = os.path.join(tmp, rf.name)
            await self._webdav.download(rf.path, local)
            transcript = await self._openai.transcribe_voice(
                local,
                self._system_user_id,
                duration_sec=None,
            )
        text = (transcript or "").strip()
        if not text or text.startswith("[") and text.endswith("]"):
            logger.warning("yandex_disk: пустая расшифровка %s", rf.name)
            return 0

        head = text[: self._head_chars]
        llm_meta = await extract_disk_material_metadata(
            curator_hint=source.hint,
            filename=rf.name,
            transcript_head=head,
            default_product=source.default_product,
            default_content_type=source.default_content_type,
        )

        source_label = llm_meta.title or rf.name
        chroma_meta = llm_meta.as_chroma_metadata(
            source_label=source_label,
            remote_path=rf.path,
        )
        chroma_meta["yandex_disk_source_id"] = source.id

        from bot.features.rag_source_visibility import (
            SOURCE_YANDEX_DISK_FOLDER,
            apply_source_link_to_metadata,
            resolve_source_visibility,
        )

        visibility = None
        if self._app is not None:
            visibility = await resolve_source_visibility(
                self._app,
                source_type=SOURCE_YANDEX_DISK_FOLDER,
                source_key=source.path,
                label=source.path,
            )
        apply_source_link_to_metadata(chroma_meta, rf.path, visibility)
        if llm_meta.summary:
            full_text = f"{llm_meta.summary}\n\n{text}"
        else:
            full_text = text

        dedupe_salt = f"ydisk:{source.id}:{rf.path}:{etag}"

        n, _ids = await self._index.add_material_text_async(
            full_text,
            base_metadata=chroma_meta,
            source=source_label,
            dedupe_salt=dedupe_salt,
        )

        await self._storage.upsert_yandex_disk_indexed(
            source_id=source.id,
            remote_path=rf.path,
            file_name=rf.name,
            etag=etag,
            file_size=file_meta.size if file_meta else 0,
            chunks_count=n,
            metadata={
                "material_kind": llm_meta.material_kind,
                "product": llm_meta.product,
                "content_type": llm_meta.content_type,
                "tags": llm_meta.tags,
            },
        )
        await self._storage.rag_import_cache_upsert(
            import_type=IMPORT_YANDEX_DISK,
            cache_key=cache_key,
            status=STATUS_INDEXED,
            chunks_count=n,
            label=source_label[:200],
            metadata={"etag": etag, "path": rf.path},
        )
        return n
