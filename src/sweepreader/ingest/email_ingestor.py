from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from sweepreader.ingest.base import BaseAdapter
from sweepreader.store.models import Item

if TYPE_CHECKING:
    from sweepreader.config import SourceConfig

logger = logging.getLogger(__name__)

_IMAP_HOST_DEFAULT = "imap.gmail.com"
_IMAP_PORT_DEFAULT = 993

_MAX_FETCH = 50  # messages per run


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            data = data.strip()
            if data:
                self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        return parser.text()[:8000]
    except Exception:
        return re.sub(r'<[^>]+>', ' ', html)[:8000]


def _extract_text(msg: email.message.Message) -> str:
    parts: list[str] = []
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset("utf-8") or "utf-8"
                try:
                    parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    parts.append(payload.decode("utf-8", errors="replace"))
        elif ct == "text/html" and not parts:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset("utf-8") or "utf-8"
                try:
                    html = payload.decode(charset, errors="replace")
                    parts.append(_html_to_text(html))
                except Exception:
                    pass
    return "\n\n".join(parts)[:8000]


def _get_delivered_to(msg: email.message.Message) -> str:
    for hdr in ("Delivered-To", "X-Original-To", "X-Forwarded-To"):
        val = msg.get(hdr, "")
        if val:
            return val.lower().strip()
    return ""


def _source_id_from_address(address: str, sources) -> str | None:
    for src in sources:
        if src.modality == "email" and src.address.lower() in address:
            return src.id
    return None


class EmailIngestor(BaseAdapter):
    """IMAP email ingestion — reads from a dedicated Gmail account."""

    def fetch(self) -> list[Item]:
        imap_user = os.environ.get("IMAP_USER", "")
        imap_password = os.environ.get("IMAP_PASSWORD", "")
        if not imap_user or not imap_password:
            logger.warning("EmailIngestor: IMAP_USER/IMAP_PASSWORD not set, skipping")
            return []

        try:
            return self._fetch_imap(imap_user, imap_password)
        except Exception as e:
            logger.error("EmailIngestor: IMAP error for source=%s: %s", self.source.id, e)
            raise

    def _watermark_key(self) -> str:
        return f"imap_uid_{self.source.id}"

    def _get_watermark(self) -> int:
        if self.state:
            return int(self.state.get(self._watermark_key(), 0))
        return 0

    def _set_watermark(self, uid: int) -> None:
        if self.state:
            self.state.set(self._watermark_key(), uid)

    def _fetch_imap(self, imap_user: str, imap_password: str) -> list[Item]:
        host = os.environ.get("IMAP_HOST", _IMAP_HOST_DEFAULT)
        port = int(os.environ.get("IMAP_PORT", str(_IMAP_PORT_DEFAULT)))
        conn = imaplib.IMAP4_SSL(host, port)
        try:
            conn.login(imap_user, imap_password)
            conn.select("INBOX", readonly=True)

            uid_watermark = self._get_watermark()
            search_str = f"UID {uid_watermark + 1}:*" if uid_watermark else "ALL"

            _, data = conn.uid("SEARCH", None, search_str)
            if not data or not data[0]:
                return []

            uids = data[0].split()
            uids = uids[-_MAX_FETCH:]  # newest N

            items: list[Item] = []
            max_uid = uid_watermark

            for uid_bytes in uids:
                uid = int(uid_bytes)
                if uid <= uid_watermark:
                    continue

                _, msg_data = conn.uid("FETCH", uid_bytes, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue

                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                delivered_to = _get_delivered_to(msg)
                if self.source.address.lower() not in delivered_to:
                    continue

                subject = email.header.decode_header(msg.get("Subject", ""))[0]
                try:
                    title = subject[0].decode(subject[1] or "utf-8") if isinstance(subject[0], bytes) else subject[0]
                except Exception:
                    title = str(subject[0])

                date_str = msg.get("Date", "")
                try:
                    pub_dt = parsedate_to_datetime(date_str).astimezone(timezone.utc)
                except Exception:
                    pub_dt = datetime.now(timezone.utc)

                raw_text = _extract_text(msg)
                msg_id = msg.get("Message-ID", f"{uid}@{self.source.id}")
                item_id = Item.make_id(self.source.id, msg_id)

                items.append(Item(
                    id=item_id,
                    source_id=self.source.id,
                    venue=self.source.id.replace("email_", "").upper(),
                    title=title.strip(),
                    url=f"mailto:{self.source.address}?message-id={msg_id}",
                    published_at=pub_dt,
                    first_seen_at=datetime.now(timezone.utc),
                    raw_text=raw_text,
                    modality="email",
                ))

                max_uid = max(max_uid, uid)

            if max_uid > uid_watermark:
                self._set_watermark(max_uid)

            return items
        finally:
            try:
                conn.logout()
            except Exception:
                pass
