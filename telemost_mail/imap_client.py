"""IMAP-клиент Яндекс.Почты (новые письма от Телемоста)."""

from __future__ import annotations

import email
import imaplib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple

from telemost_mail.email_parse import is_telemost_sender

from telemost_mail.video_attach import extract_video_bytes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchedMail:
    uid: str
    message_id: str
    subject: str
    sender: str
    received_at: Optional[datetime]
    body_text: str
    transcript_text: str
    video_bytes: bytes = b""
    video_filename: str = ""


def _decode_mime_header(raw: Optional[str]) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    out: List[str] = []
    for data, enc in parts:
        if isinstance(data, bytes):
            out.append(data.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(data))
    return "".join(out).strip()


def _html_to_plain(html: str) -> str:
    import re

    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?is)<br\s*/?>", "\n", t)
    t = re.sub(r"(?is)</p\s*>", "\n\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _extract_body_and_txt(msg: Message) -> Tuple[str, str]:
    body_parts: List[str] = []
    txt_attachments: List[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            fname = part.get_filename() or ""
            if "attachment" in disp or fname:
                if fname.lower().endswith(".txt") or ctype == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        txt_attachments.append(
                            payload.decode(charset, errors="replace").strip()
                        )
                continue
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_parts.append(payload.decode(charset, errors="replace"))
            elif ctype == "text/html" and not body_parts:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_parts.append(
                        _html_to_plain(
                            payload.decode(charset, errors="replace")
                        )
                    )
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if (msg.get_content_type() or "").lower() == "text/html":
                text = _html_to_plain(text)
            body_parts.append(text)

    body = "\n\n".join(p.strip() for p in body_parts if p.strip())
    transcript = ""
    if txt_attachments:
        transcript = max(txt_attachments, key=len)
    return body.strip(), transcript.strip()


def _message_to_fetched(uid: str, raw: bytes) -> Optional[FetchedMail]:
    try:
        msg = email.message_from_bytes(raw)
    except Exception as e:
        logger.warning("telemost_mail: parse message: %s", e)
        return None
    subject = _decode_mime_header(msg.get("Subject"))
    sender = _decode_mime_header(msg.get("From"))
    mid = (msg.get("Message-ID") or "").strip()
    received_at = None
    try:
        dt = parsedate_to_datetime(msg.get("Date") or "")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        received_at = dt
    except Exception:
        pass
    body, transcript = _extract_body_and_txt(msg)
    video_bytes, video_filename = extract_video_bytes(msg)
    return FetchedMail(
        uid=str(uid),
        message_id=mid,
        subject=subject,
        sender=sender,
        received_at=received_at,
        body_text=body,
        transcript_text=transcript,
        video_bytes=video_bytes,
        video_filename=video_filename,
    )


class YandexImapClient:
    def __init__(
        self,
        login: str,
        password: str,
        *,
        host: str = "imap.yandex.ru",
        port: int = 993,
        folder: str = "INBOX",
    ):
        self._login = (login or "").strip()
        self._password = (password or "").strip()
        self._host = host
        self._port = int(port)
        self._folder = folder or "INBOX"

    @property
    def configured(self) -> bool:
        return bool(self._login and self._password)

    def fetch_since_date(
        self,
        since: datetime,
        *,
        from_markers: Tuple[str, ...] = ("telemost", "телемост"),
        limit: int = 100,
    ) -> List[FetchedMail]:
        """Письма с даты ``since`` (для догрузки)."""
        if not self.configured:
            return []
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_str = since.strftime("%d-%b-%Y")
        markers = tuple(m.lower() for m in from_markers if m)
        out: List[FetchedMail] = []
        conn: Optional[imaplib.IMAP4_SSL] = None
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._port)
            conn.login(self._login, self._password)
            conn.select(self._folder)
            typ, data = conn.uid("search", None, f'(SINCE "{since_str}")')
            if typ != "OK" or not data or not data[0]:
                return []
            uids = [u.decode() for u in data[0].split()]
            uids = uids[-limit:]
            for uid in uids:
                typ, msg_data = conn.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                fetched = _message_to_fetched(uid, raw)
                if not fetched:
                    continue
                if fetched.received_at and fetched.received_at < since:
                    continue
                if not is_telemost_sender(fetched.sender, markers):
                    subj_low = (fetched.subject or "").lower()
                    if "конспект встречи" not in subj_low and "запись встречи" not in subj_low:
                        continue
                if not fetched.body_text and not fetched.transcript_text:
                    continue
                out.append(fetched)
            return out
        except Exception as e:
            logger.error("telemost_mail IMAP fetch_since_date: %s", e, exc_info=True)
            return out
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass

    def fetch_new_since_uid(
        self,
        *,
        min_uid_exclusive: int = 0,
        from_markers: Tuple[str, ...] = ("telemost", "телемост"),
        limit: int = 20,
    ) -> List[FetchedMail]:
        """Синхронный IMAP (вызывать через asyncio.to_thread)."""
        if not self.configured:
            return []
        markers = tuple(m.lower() for m in from_markers if m)
        out: List[FetchedMail] = []
        conn: Optional[imaplib.IMAP4_SSL] = None
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._port)
            conn.login(self._login, self._password)
            conn.select(self._folder)
            typ, data = conn.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return []
            uids = [u.decode() for u in data[0].split()]
            uids = [u for u in uids if int(u) > int(min_uid_exclusive)]
            uids = uids[-limit:]
            for uid in uids:
                typ, msg_data = conn.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                fetched = _message_to_fetched(uid, raw)
                if not fetched:
                    continue
                if not is_telemost_sender(fetched.sender, markers):
                    subj_low = (fetched.subject or "").lower()
                    if "конспект встречи" not in subj_low and "запись встречи" not in subj_low:
                        continue
                if not fetched.body_text and not fetched.transcript_text:
                    continue
                out.append(fetched)
            return out
        except Exception as e:
            logger.error("telemost_mail IMAP: %s", e, exc_info=True)
            return out
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass

    def fetch_recent(
        self,
        *,
        from_markers: Tuple[str, ...] = ("telemost", "телемост"),
        limit: int = 30,
    ) -> List[FetchedMail]:
        """Последние N писем (для синхронизации записей без привязки к last_uid)."""
        if not self.configured:
            return []
        markers = tuple(m.lower() for m in from_markers if m)
        out: List[FetchedMail] = []
        conn: Optional[imaplib.IMAP4_SSL] = None
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._port)
            conn.login(self._login, self._password)
            conn.select(self._folder)
            typ, data = conn.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return []
            uids = [u.decode() for u in data[0].split()][-limit:]
            for uid in uids:
                typ, msg_data = conn.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                fetched = _message_to_fetched(uid, raw)
                if not fetched:
                    continue
                if not is_telemost_sender(fetched.sender, markers):
                    subj_low = (fetched.subject or "").lower()
                    if "конспект встречи" not in subj_low and "запись встречи" not in subj_low:
                        continue
                if not fetched.body_text and not fetched.transcript_text:
                    continue
                out.append(fetched)
            return out
        except Exception as e:
            logger.error("telemost_mail IMAP fetch_recent: %s", e, exc_info=True)
            return out
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass

    def max_uid(self) -> int:
        if not self.configured:
            return 0
        conn: Optional[imaplib.IMAP4_SSL] = None
        try:
            conn = imaplib.IMAP4_SSL(self._host, self._port)
            conn.login(self._login, self._password)
            conn.select(self._folder)
            typ, data = conn.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return 0
            uids = [int(u) for u in data[0].split()]
            return max(uids) if uids else 0
        except Exception as e:
            logger.error("telemost_mail IMAP max_uid: %s", e)
            return 0
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
