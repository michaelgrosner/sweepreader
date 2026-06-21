from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import feedparser
import httpx

from sweepreader.ingest import cboe
from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.store.models import Item

if TYPE_CHECKING:
    from sweepreader.config import SourceConfig

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 14

_VENUE_FROM_SOURCE: dict[str, str] = {
    "cboe_options_tech": "CBOE",
    "cboe_equities_tech": "CBOE",
    "cboe_futures_tech": "CBOE",
    "nasdaqtrader_options_tech": "NASDAQ",
    "nasdaqtrader_options_reg": "NASDAQ",
    "nasdaqtrader_equity_tech": "NASDAQ",
    "nasdaqtrader_data_tech": "NASDAQ",
    "nasdaqtrader_halt": "NASDAQ",
    "occ_alerts": "OCC",
    "cat_nms": "CAT",
    "finra_regulatory": "FINRA",
    "sec_press": "SEC",
    "memx_notices": "MEMX",
    "box_notices": "BOX",
}


def _parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            import time
            try:
                return datetime.fromtimestamp(time.mktime(val), tz=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return parsedate_to_datetime(val).astimezone(timezone.utc)
            except Exception:
                pass
    return None


_DOUBLE_SCHEME = re.compile(r'^https?://[^/]+/(https?://.+)$')


def _clean_url(url: str) -> str:
    """Fix feedparser resolving absolute link URLs relative to a CDN base,
    producing cdn.example.com/https://real.example.com/path."""
    m = _DOUBLE_SCHEME.match(url)
    return m.group(1) if m else url


def _title_from_url(url: str) -> str:
    """Extract a human-readable title from a URL when the feed provides none."""
    path = urlparse(url).path
    name = path.rstrip("/").split("/")[-1]
    # Remove extension and replace separators with spaces
    name = re.sub(r'\.[a-zA-Z0-9]+$', '', name)
    name = re.sub(r'[-_]', ' ', name)
    return name.strip().title() if name else url


def _entry_text(entry) -> str:
    parts = [entry.get("title", "")]
    summary = entry.get("summary", "") or entry.get("description", "")
    if summary:
        parts.append(summary)
    content = entry.get("content", [])
    if content:
        parts.append(content[0].get("value", ""))
    text = "\n\n".join(p for p in parts if p)
    return text[:8000]


class RssAdapter(BaseAdapter):
    def fetch(self) -> list[Item]:
        resp = httpx.get(
            self.source.endpoint,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=30.0,
            follow_redirects=True,
        )
        resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        if feed.bozo and not feed.entries:
            raise ValueError(f"Feed parse error for {self.source.endpoint}: {feed.bozo_exception}")

        venue = _VENUE_FROM_SOURCE.get(self.source.id, self.source.id.upper())
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)
        items: list[Item] = []

        for entry in feed.entries:
            url = _clean_url(entry.get("link", "") or entry.get("id", ""))
            if not url:
                continue

            pub_dt = _parse_date(entry)

            # Skip items with no parseable date or older than lookback window
            if pub_dt is None or pub_dt < cutoff:
                continue

            title = entry.get("title", "").strip()
            raw_text = _entry_text(entry)

            # Cboe spec pages carry an empty RSS title and no body; pull the
            # latest change-log row from the spec's revision-history page so the
            # classifier sees a dated summary instead of just a filename.
            if self.source.id.startswith("cboe"):
                enriched = cboe.enrich(url)
                if enriched is not None:
                    title = title or enriched.spec_title or title
                    raw_text = enriched.raw_text()

            if not title:
                title = _title_from_url(url)

            item_id = Item.make_id(self.source.id, url)

            items.append(Item(
                id=item_id,
                source_id=self.source.id,
                venue=venue,
                title=title,
                url=url,
                published_at=pub_dt,
                first_seen_at=now,
                raw_text=raw_text,
                modality="rss",
            ))

        return items
