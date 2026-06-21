"""Scrape adapter for MIAX alert pages.

MIAX exposes no usable feed, but its alert listings are plain server-rendered
Drupal HTML (no JS needed). Each listing page

    https://www.miaxglobal.com/markets/<market>/.../alerts

renders one ``<article about="/alert/YYYY/MM/DD/<slug>">`` teaser per alert,
carrying the alert type(s), the specific MIAX venue, and the title; the
publication date is in the URL path. We read the listing for the item list, then
fetch each alert's detail page for the full body to hand the classifier. The
listing paginates with ``?page=N`` (newest first), which the `seed` CLI walks for
historical backfill (SPEC §5). This covers the MIAX venues that SPEC §2 otherwise
routed to Tier-2 email.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterator

import httpx
from selectolax.parser import HTMLParser, Node

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.ingest.html_text import html_to_text
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_BASE = "https://www.miaxglobal.com"
_LOOKBACK_DAYS = 14
_ALERT_PATH_RE = re.compile(r"^/alert/(\d{4})/(\d{2})/(\d{2})/")


@dataclass(frozen=True)
class ParsedAlert:
    url: str
    published_at: datetime
    title: str
    venue: str
    alert_types: str


def _text(node: Node, selector: str) -> str:
    el = node.css_first(selector)
    return " ".join(el.text().split()) if el else ""


def _parse_article(art: Node) -> ParsedAlert | None:
    path = (art.attributes.get("about") or "").strip()
    m = _ALERT_PATH_RE.match(path)
    if not m:
        return None
    try:
        pub_dt = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
    except ValueError:
        return None
    return ParsedAlert(
        url=_BASE + path,
        published_at=pub_dt,
        title=_text(art, ".heading-text") or _title_from_path(path),
        venue=_text(art, ".alert-exchange-type-items") or "MIAX",
        alert_types=_text(art, ".alert-type-items"),
    )


def _build_item(source_id: str, alert: ParsedAlert, body: str, first_seen_at: datetime) -> Item:
    header = " — ".join(p for p in (alert.venue, alert.alert_types, alert.title) if p)
    raw_text = f"{header}\n{body}"[:8000] if body else header
    return Item(
        id=Item.make_id(source_id, alert.url),
        source_id=source_id,
        venue=alert.venue,
        title=alert.title,
        url=alert.url,
        published_at=alert.published_at,
        first_seen_at=first_seen_at,
        raw_text=raw_text,
        modality="scrape",
    )


def _get(url: str, timeout: float = 30.0) -> str:
    resp = httpx.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


def _detail_body(url: str) -> str:
    try:
        tree = HTMLParser(_get(url))
        art = tree.css_first("article.node--view-mode-full")
        return html_to_text(art.html if art else tree.html)
    except Exception as e:  # per-item isolation (SPEC §10)
        logger.warning("miax detail fetch failed for %s: %s", url, e)
        return ""


def _parse_listing(html: str) -> list[ParsedAlert]:
    tree = HTMLParser(html)
    out: list[ParsedAlert] = []
    for art in tree.css("article.node--type-alert"):
        parsed = _parse_article(art)
        if parsed is not None:
            out.append(parsed)
    return out


def iter_seed_items(source_id: str, endpoint: str, *, stop_before: datetime,
                    max_pages: int = 400, sleep: float = 0.3) -> Iterator[Item]:
    """Walk listing pages newest-first, yielding items (first_seen = published)
    until alerts predate `stop_before`. Used by the seed CLI for backtesting."""
    seen: set[str] = set()
    for page in range(max_pages):
        sep = "&" if "?" in endpoint else "?"
        alerts = _parse_listing(_get(f"{endpoint}{sep}page={page}"))
        if not alerts:
            return
        new_on_page = 0
        for alert in alerts:
            if alert.url in seen:
                continue
            seen.add(alert.url)
            if alert.published_at < stop_before:
                continue
            new_on_page += 1
            yield _build_item(source_id, alert, _detail_body(alert.url), alert.published_at)
        # Pages are newest-first; once an entire page is older than the cutoff, stop.
        if page > 0 and new_on_page == 0 and all(a.published_at < stop_before for a in alerts):
            return
        time.sleep(sleep)


class MiaxAdapter(BaseAdapter):
    def fetch(self) -> list[Item]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)
        items: list[Item] = []
        seen: set[str] = set()
        for alert in _parse_listing(_get(self.source.endpoint)):
            if alert.url in seen or alert.published_at < cutoff:
                continue
            seen.add(alert.url)
            items.append(_build_item(self.source.id, alert, _detail_body(alert.url), now))
        return items


def _title_from_path(path: str) -> str:
    slug = path.rstrip("/").split("/")[-1]
    return re.sub(r"-\d+$", "", slug).replace("-", " ").strip().title()
