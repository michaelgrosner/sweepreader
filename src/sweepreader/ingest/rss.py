from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import feedparser
import httpx

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.store.models import Item

if TYPE_CHECKING:
    from sweepreader.config import SourceConfig

logger = logging.getLogger(__name__)

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
}


def _parse_date(entry) -> datetime:
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
    return datetime.now(timezone.utc)


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
        items: list[Item] = []

        for entry in feed.entries:
            url = entry.get("link", "") or entry.get("id", "")
            if not url:
                continue
            title = entry.get("title", "").strip()
            pub_dt = _parse_date(entry)
            raw_text = _entry_text(entry)
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
