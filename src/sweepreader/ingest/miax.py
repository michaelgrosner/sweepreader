"""Scrape adapter for MIAX alert pages.

MIAX exposes no usable feed, but its alert listings are plain server-rendered
Drupal HTML (no JS needed). Each listing page

    https://www.miaxglobal.com/markets/<market>/.../alerts

renders one ``<article about="/alert/YYYY/MM/DD/<slug>">`` teaser per alert,
carrying the alert type(s), the specific MIAX venue, and the title; the
publication date is in the URL path. We read the listing for the item list, then
fetch each alert's detail page for the full body to hand the classifier. This
covers the MIAX venues that SPEC §2 otherwise routed to Tier-2 email.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone, timedelta

import httpx

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_BASE = "https://www.miaxglobal.com"
_LOOKBACK_DAYS = 14

# One alert teaser; captures the alert path (with the date embedded) and the block.
_ARTICLE_RE = re.compile(
    r'<article\b[^>]*\babout="(/alert/(\d{4})/(\d{2})/(\d{2})/[^"]+)"[^>]*>(.*?)</article>',
    re.S,
)
# The full-detail article wrapping an alert's body on its own page.
_DETAIL_RE = re.compile(r'<article\b[^>]*node--view-mode-full.*?</article>', re.S)
_SCRIPT_STYLE_RE = re.compile(r'<(script|style|svg)\b[^>]*>.*?</\1>', re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')


def _field(block: str, cls: str) -> str:
    """First text run inside the element bearing CSS class `cls`."""
    m = re.search(r'class="[^"]*' + re.escape(cls) + r'[^"]*"[^>]*>([^<]*)', block)
    return _clean(m.group(1)) if m else ""


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _strip_html(fragment: str) -> str:
    fragment = _SCRIPT_STYLE_RE.sub(" ", fragment)
    return _clean(_TAG_RE.sub(" ", fragment))


def _detail_text(detail_html: str) -> str:
    m = _DETAIL_RE.search(detail_html)
    return _strip_html(m.group(0) if m else detail_html)[:8000]


class MiaxAdapter(BaseAdapter):
    def fetch(self) -> list[Item]:
        listing = self._get(self.source.endpoint)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)
        items: list[Item] = []
        seen: set[str] = set()

        for path, yyyy, mm, dd, block in _ARTICLE_RE.findall(listing):
            url = _BASE + path
            if url in seen:  # the same alert can appear twice (e.g. featured + list)
                continue
            seen.add(url)

            try:
                pub_dt = datetime(int(yyyy), int(mm), int(dd), tzinfo=timezone.utc)
            except ValueError:
                continue
            if pub_dt < cutoff:
                continue

            title = _field(block, "heading-text") or _title_from_path(path)
            venue = _field(block, "alert-exchange-type-items") or "MIAX"
            alert_types = _field(block, "alert-type-items")

            raw_text = self._detail(url)
            header = " — ".join(p for p in (venue, alert_types, title) if p)
            raw_text = f"{header}\n{raw_text}"[:8000] if raw_text else header

            items.append(Item(
                id=Item.make_id(self.source.id, url),
                source_id=self.source.id,
                venue=venue,
                title=title,
                url=url,
                published_at=pub_dt,
                first_seen_at=now,
                raw_text=raw_text,
                modality="scrape",
            ))

        return items

    def _detail(self, url: str) -> str:
        try:
            return _detail_text(self._get(url))
        except Exception as e:  # per-item isolation (SPEC §10): fall back to teaser-less item
            logger.warning("miax detail fetch failed for %s: %s", url, e)
            return ""

    @staticmethod
    def _get(url: str) -> str:
        resp = httpx.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=30.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text


def _title_from_path(path: str) -> str:
    slug = path.rstrip("/").split("/")[-1]
    return re.sub(r"-\d+$", "", slug).replace("-", " ").strip().title()
