"""Клиент Яндекс.Диска по WebDAV (логин + пароль приложения)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import quote, unquote

import httpx

logger = logging.getLogger(__name__)

_DAV_NS = {"d": "DAV:"}


@dataclass(frozen=True)
class RemoteFile:
    path: str
    name: str
    etag: str
    modified: Optional[object]
    size: int


@dataclass(frozen=True)
class _DavEntry:
    href: str
    is_collection: bool
    etag: str
    size: int
    modified: Optional[object]


class YandexDiskWebDAV:
    def __init__(
        self,
        login: str,
        password: str,
        *,
        base_url: str = "https://webdav.yandex.ru",
        timeout_sec: float = 120.0,
    ):
        self._login = (login or "").strip()
        self._password = (password or "").strip()
        self._base = (base_url or "https://webdav.yandex.ru").rstrip("/")
        self._timeout = timeout_sec

    @property
    def configured(self) -> bool:
        return bool(self._login and self._password)

    def _url(self, remote_path: str) -> str:
        p = remote_path if remote_path.startswith("/") else f"/{remote_path}"
        parts = [quote(seg, safe="") for seg in p.split("/") if seg]
        return f"{self._base}/{'/'.join(parts)}"

    def _norm_href(self, href: str) -> str:
        h = unquote((href or "").strip())
        if h.startswith("http"):
            idx = h.find("/", len(self._base))
            h = h[idx:] if idx > 0 else h
        if not h.startswith("/"):
            h = "/" + h
        return h.rstrip("/") or "/"

    async def list_files(
        self,
        remote_dir: str,
        *,
        recursive: bool = False,
    ) -> List[RemoteFile]:
        if not self.configured:
            return []
        dir_path = self._norm_href(remote_dir)
        out: List[RemoteFile] = []

        async with httpx.AsyncClient(
            auth=(self._login, self._password),
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            if recursive:
                await self._walk(client, dir_path, out)
            else:
                for ent in await self._propfind(client, dir_path):
                    if ent.is_collection:
                        continue
                    out.append(self._to_remote_file(ent))

        return out

    async def _walk(
        self,
        client: httpx.AsyncClient,
        dir_path: str,
        acc: List[RemoteFile],
    ) -> None:
        for ent in await self._propfind(client, dir_path):
            if ent.is_collection:
                if ent.href.rstrip("/") == dir_path.rstrip("/"):
                    continue
                await self._walk(client, ent.href, acc)
            else:
                acc.append(self._to_remote_file(ent))

    async def _propfind(
        self,
        client: httpx.AsyncClient,
        dir_path: str,
    ) -> List[_DavEntry]:
        url = self._url(dir_path)
        body = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:resourcetype/>
    <d:getetag/>
    <d:getlastmodified/>
    <d:getcontentlength/>
  </d:prop>
</d:propfind>"""
        r = await client.request(
            "PROPFIND",
            url,
            content=body,
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        if r.status_code == 404:
            logger.warning("WebDAV 404: папка не найдена: %s", dir_path)
            return []
        r.raise_for_status()
        return self._parse_propfind(r.text)

    def _parse_propfind(self, xml_text: str) -> List[_DavEntry]:
        entries: List[_DavEntry] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.warning("WebDAV: не разобрали XML PROPFIND")
            return entries

        for resp in root.findall("d:response", _DAV_NS):
            href_el = resp.find("d:href", _DAV_NS)
            if href_el is None or not href_el.text:
                continue
            href = self._norm_href(href_el.text)
            propstat = resp.find("d:propstat", _DAV_NS)
            if propstat is None:
                continue
            status = propstat.find("d:status", _DAV_NS)
            if status is not None and "200" not in (status.text or ""):
                continue
            prop = propstat.find("d:prop", _DAV_NS)
            if prop is None:
                continue
            rt = prop.find("d:resourcetype", _DAV_NS)
            is_dir = rt is not None and rt.find("d:collection", _DAV_NS) is not None
            etag_el = prop.find("d:getetag", _DAV_NS)
            etag = (etag_el.text or "").strip('"') if etag_el is not None else ""
            size = 0
            sz_el = prop.find("d:getcontentlength", _DAV_NS)
            if sz_el is not None and sz_el.text:
                try:
                    size = int(sz_el.text)
                except ValueError:
                    size = 0
            modified = None
            lm_el = prop.find("d:getlastmodified", _DAV_NS)
            if lm_el is not None and lm_el.text:
                try:
                    modified = parsedate_to_datetime(lm_el.text.strip())
                    if modified.tzinfo is None:
                        modified = modified.replace(tzinfo=timezone.utc)
                except Exception:
                    modified = None
            entries.append(
                _DavEntry(
                    href=href,
                    is_collection=is_dir,
                    etag=etag,
                    size=size,
                    modified=modified,
                )
            )
        return entries

    def _to_remote_file(self, ent: _DavEntry) -> RemoteFile:
        name = ent.href.rstrip("/").split("/")[-1]
        return RemoteFile(
            path=ent.href,
            name=name,
            etag=ent.etag,
            modified=ent.modified,
            size=ent.size,
        )

    async def download(self, remote_path: str, local_path: str) -> None:
        if not self.configured:
            raise RuntimeError("Yandex Disk WebDAV: нет логина/пароля")
        url = self._url(remote_path)
        async with httpx.AsyncClient(
            auth=(self._login, self._password),
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with open(local_path, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)

    async def get_file_meta(self, remote_path: str) -> Optional[RemoteFile]:
        if not self.configured:
            return None
        path = self._norm_href(remote_path)
        parent = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
        async with httpx.AsyncClient(
            auth=(self._login, self._password),
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            for ent in await self._propfind(client, parent):
                if ent.href.rstrip("/") == path.rstrip("/") and not ent.is_collection:
                    return self._to_remote_file(ent)
        return None
