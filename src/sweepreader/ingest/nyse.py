"""NYSE Trader Updates adapter (public notifications JSON API).

NYSE's "Trader Updates" history page (nyse.com/trader-update/history) renders its
list client-side from a public, paginated, date-sorted JSON API discovered behind
the page's `notification-history-2023` CMS component:

    GET /api/notifications/public/system/{systemId}/summaries/filter
        ?pageSize&pageNumber&sortKey=publishedDate&sortOrder=desc
        [&searchKey&marketIds&serviceIds]

`systemId=1` is the trader-update feed. Each record carries `subject`, the full
HTML `body`, epoch-ms `publishedDate`, and `marketLinks`/`serviceLinks` tags — so
no per-item detail fetch is needed. ~18.5k records reach back to 2006, which the
`seed` CLI pages for historical backfill (SPEC §5 backtesting).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Iterator

import httpx

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.ingest.html_text import html_to_text
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_SYSTEM_ID = 1
_BASE = "https://www.nyse.com"
_LIST_PATH = "/api/notifications/public/system/{sid}/summaries/filter"
_LOOKBACK_DAYS = 14
_PAGE_SIZE = 50
_MAX_LIVE_PAGES = 6  # plenty for a 14-day window; the seeder pages without this cap


def _published_at(notification: dict) -> datetime:
    return datetime.fromtimestamp(notification["publishedDate"] / 1000, tz=timezone.utc)


def _venue(notification: dict) -> str:
    markets = notification.get("marketLinks") or []
    if not markets:
        return "NYSE"
    # Tags arrive upper-cased ("NYSE ARCA OPTIONS"); title-case but keep "NYSE".
    pretty = [m.title().replace("Nyse", "NYSE") for m in markets]
    return " / ".join(pretty)


def notification_to_item(notification: dict, source_id: str, *, first_seen_at: datetime) -> Item:
    nid = notification["id"]
    # No public per-notification page exists; the history page + id fragment is a
    # stable, unique, human-resolvable canonical link.
    url = f"{_BASE}/trader-update/history#{nid}"
    subject = (notification.get("subject") or "").strip()
    markets = ", ".join(notification.get("marketLinks") or [])
    services = ", ".join(notification.get("serviceLinks") or [])
    body = html_to_text(notification.get("body") or "")

    tag_line = "; ".join(p for p in (f"Markets: {markets}" if markets else "",
                                     f"Services: {services}" if services else "") if p)
    raw_text = "\n".join(p for p in (subject, tag_line, body) if p)[:8000]

    return Item(
        id=Item.make_id(source_id, url),
        source_id=source_id,
        venue=_venue(notification),
        title=subject or f"NYSE Trader Update {nid}",
        url=url,
        published_at=_published_at(notification),
        first_seen_at=first_seen_at,
        raw_text=raw_text,
        modality="api",
    )


def fetch_page(page: int, *, system_id: int = _SYSTEM_ID, page_size: int = _PAGE_SIZE,
               timeout: float = 30.0) -> tuple[list[dict], int]:
    """One page of notifications, newest first. Returns (records, total_count)."""
    url = _BASE + _LIST_PATH.format(sid=system_id)
    resp = httpx.get(
        url,
        params={
            "pageSize": page_size,
            "pageNumber": page,
            "sortKey": "publishedDate",
            "sortOrder": "desc",
        },
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"},
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("data", []), int(payload.get("totalCount", 0))


def iter_notifications(*, stop_before: datetime | None = None, max_pages: int | None = None,
                       system_id: int = _SYSTEM_ID, page_size: int = _PAGE_SIZE) -> Iterator[dict]:
    """Yield notifications newest-first, stopping once `publishedDate` predates
    `stop_before` or `max_pages` is reached (whichever comes first)."""
    page = 0
    while max_pages is None or page < max_pages:
        records, total = fetch_page(page, system_id=system_id, page_size=page_size)
        if not records:
            return
        for n in records:
            if stop_before is not None and _published_at(n) < stop_before:
                return
            yield n
        if (page + 1) * page_size >= total:
            return
        page += 1


def iter_seed_items(source_id: str, *, stop_before: datetime) -> Iterator[Item]:
    """Page the full history back to `stop_before`, yielding items whose
    first_seen = published date (historical reconstruction for backtesting)."""
    for n in iter_notifications(stop_before=stop_before):
        yield notification_to_item(n, source_id, first_seen_at=_published_at(n))


class NyseAdapter(BaseAdapter):
    def fetch(self) -> list[Item]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)
        return [
            notification_to_item(n, self.source.id, first_seen_at=now)
            for n in iter_notifications(stop_before=cutoff, max_pages=_MAX_LIVE_PAGES)
        ]
