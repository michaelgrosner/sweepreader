"""OPRA notices adapter (opraplan.com homepage).

OPRA lists every notice — all years (2015–present) — in a single table on its
homepage, each row a date plus a link to the notice PDF on
``cdn.opraplan.com/documents/notices/…``. So one page fetch yields the full
history: good for the live 14-day window and fully seedable with no pagination.
The PDF holds the real notice body, extracted via the shared `pdf_text` helper.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Iterator

import httpx
from selectolax.parser import HTMLParser

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.ingest.pdf_text import pdf_to_text
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 14
_NOTICE_MARKER = "/documents/notices/"


def _parse_date(text: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_homepage(html: str) -> list[dict]:
    """Extract notice rows (date, title, pdf_url) from the homepage table."""
    tree = HTMLParser(html)
    rows: list[dict] = []
    seen: set[str] = set()
    for tr in tree.css("tr"):
        link = tr.css_first("a")
        if link is None:
            continue
        pdf_url = (link.attributes.get("href") or "").strip()
        if _NOTICE_MARKER not in pdf_url or pdf_url in seen:
            continue
        cells = tr.css("td")
        published_at = _parse_date(cells[0].text()) if cells else None
        title = " ".join(link.text().split())
        if not title:
            continue
        seen.add(pdf_url)
        rows.append({"title": title, "published_at": published_at, "pdf_url": pdf_url})
    return rows


def _get_text(url: str, cache=None, timeout: float = 30.0) -> str:
    if cache is not None:
        return cache.fetch_text(url, timeout=timeout)
    resp = httpx.get(url, headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
                     timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _pdf_text(url: str, cache=None, timeout: float = 30.0) -> str:
    try:
        if cache is not None:
            data = cache.fetch_bytes(url, timeout=timeout)
        else:
            resp = httpx.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout,
                             follow_redirects=True)
            resp.raise_for_status()
            data = resp.content
        return pdf_to_text(data)
    except Exception as e:  # per-item isolation (SPEC §10)
        logger.warning("opra pdf fetch failed for %s: %s", url, e)
        return ""


def _build_item(source_id: str, row: dict, pub_dt: datetime, first_seen_at: datetime, cache=None) -> Item:
    body = _pdf_text(row["pdf_url"], cache=cache)
    raw_text = f"{row['title']}\n{body}"[:8000] if body else row["title"]
    return Item(
        id=Item.make_id(source_id, row["pdf_url"]),
        source_id=source_id,
        venue="OPRA",
        title=row["title"],
        url=row["pdf_url"],
        published_at=pub_dt,
        first_seen_at=first_seen_at,
        raw_text=raw_text,
        modality="scrape",
    )


def iter_seed_items(source_id: str, endpoint: str, *, stop_before: datetime, cache=None) -> Iterator[Item]:
    """Historical backfill: first_seen = published date. The whole notice history
    is on one page, so this is a single homepage fetch plus one PDF per notice."""
    for row in parse_homepage(_get_text(endpoint, cache=cache)):
        pub_dt = row["published_at"]
        if pub_dt is None or pub_dt < stop_before:
            continue
        yield _build_item(source_id, row, pub_dt, pub_dt, cache=cache)


class OpraAdapter(BaseAdapter):
    def fetch(self) -> list[Item]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)
        items: list[Item] = []
        for row in parse_homepage(_get_text(self.source.endpoint)):
            pub_dt = row["published_at"]
            if pub_dt is None or pub_dt < cutoff:
                continue
            items.append(_build_item(self.source.id, row, pub_dt, now))
        return items
