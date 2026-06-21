"""IEX Trading Alerts adapter (public notifications JSON API).

IEX's trading-alerts site (notifications.iex.io/tradingalerts) is a Next.js app
that loads from a public, paginated JSON API:

    GET https://api.notifications.iex.io/api/v1/public/trading-alerts?page=N&limit=M

Each item carries `title`, full HTML `content`, `category`, `venue`, `alert_id`,
and ISO `published_at`/`alert_date` — content inline, so no detail fetch is
needed. Per-item pages resolve at notifications.iex.io/tradingalerts/<id>. The
non-`/public/` routes are auth-gated (403); the public route is open.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Iterator

import httpx

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.ingest.html_text import html_to_text
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_API = "https://api.notifications.iex.io/api/v1/public/trading-alerts"
_SITE = "https://notifications.iex.io/tradingalerts"
_LOOKBACK_DAYS = 14
_LIMIT = 50
_MAX_LIVE_PAGES = 4  # the feed is small; the seeder pages without this cap


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _published_at(alert: dict) -> datetime | None:
    return _parse_dt(alert.get("published_at") or alert.get("alert_date") or alert.get("created_at"))


def alert_to_item(alert: dict, source_id: str, *, first_seen_at: datetime) -> Item:
    aid = alert["id"]
    url = f"{_SITE}/{aid}"
    title = (alert.get("title") or "").strip()
    venue = ("IEX " + (alert.get("venue") or "")).strip() or "IEX"
    tags = "; ".join(p for p in (
        f"Category: {alert['category']}" if alert.get("category") else "",
        f"Alert {alert['alert_id']}" if alert.get("alert_id") else "",
    ) if p)
    body = html_to_text(alert.get("content") or "")
    raw_text = "\n".join(p for p in (title, tags, body) if p)[:8000]

    return Item(
        id=Item.make_id(source_id, url),
        source_id=source_id,
        venue=venue,
        title=title or f"IEX Trading Alert {aid}",
        url=url,
        published_at=_published_at(alert) or first_seen_at,
        first_seen_at=first_seen_at,
        raw_text=raw_text,
        modality="api",
    )


def fetch_page(page: int, *, limit: int = _LIMIT, timeout: float = 30.0,
               cache=None) -> tuple[list[dict], int]:
    """One page of alerts. Returns (items, total_pages)."""
    params = {"page": page, "limit": limit}
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"}
    if cache is not None:
        payload = json.loads(cache.fetch_text(_API, params=params, headers=headers, timeout=timeout))
    else:
        resp = httpx.get(_API, params=params, headers=headers, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
    return payload.get("items", []), int(payload.get("pages", 1))


def iter_alerts(*, stop_before: datetime | None = None, max_pages: int | None = None,
                limit: int = _LIMIT, cache=None) -> Iterator[dict]:
    """Yield alerts newest-first, stopping once `published_at` predates
    `stop_before` or `max_pages`/last page is reached."""
    page = 1
    while max_pages is None or page <= max_pages:
        items, pages = fetch_page(page, limit=limit, cache=cache)
        if not items:
            return
        for a in items:
            pub = _published_at(a)
            if stop_before is not None and pub is not None and pub < stop_before:
                return
            yield a
        if page >= pages:
            return
        page += 1


def iter_seed_items(source_id: str, *, stop_before: datetime, cache=None) -> Iterator[Item]:
    """Historical backfill: first_seen = published date (content inline, no gate)."""
    for a in iter_alerts(stop_before=stop_before, cache=cache):
        yield alert_to_item(a, source_id, first_seen_at=_published_at(a) or stop_before)


class IexAdapter(BaseAdapter):
    def fetch(self) -> list[Item]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)
        return [
            alert_to_item(a, self.source.id, first_seen_at=now)
            for a in iter_alerts(stop_before=cutoff, max_pages=_MAX_LIVE_PAGES)
        ]
