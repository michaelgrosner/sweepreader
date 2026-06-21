"""Scrape adapter for BOX Exchange notices (regulatory circulars).

BOX migrated to WordPress. The ``/notices`` listing is the only place that
carries the per-notice circular number, category, and the "View Document" PDF
link; the RSS feed and single-notice pages omit all of that (the feed is
title-only, the single template renders an empty body). The listing is behind a
Cloudflare *header* gate (not an interactive captcha) — a complete set of
browser ``sec-ch-ua``/``Sec-Fetch-*`` headers passes it.

Each ``<article class="circulars …">`` row gives the title, circular number,
date, categories (from ``notice_category-*`` classes), and a PDF URL like
``/assets/Notice-2026-059-Weekend-Testing-Dates.pdf``. The PDF holds the real
notice text, which we extract with pypdf for the classifier.

If the listing is blocked (e.g. a stricter Cloudflare challenge from a CI IP),
we fall back to the unprotected RSS feed for title-only items so BOX still
produces something rather than vanishing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import feedparser
import httpx

from sweepreader.ingest.base import BaseAdapter
from sweepreader.ingest.pdf_text import pdf_to_text
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 14
_FEED_URL = "https://boxexchange.com/?post_type=circulars&feed=rss2"

# A full browser header set; BOX's Cloudflare rule gates on client hints, so the
# sec-ch-ua / Sec-Fetch-* headers are what get us past it.
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}
_CHALLENGE_MARKERS = ("attention required", "just a moment", "cf_chl", "challenge-platform")


def _is_challenge(html: str) -> bool:
    low = html[:4000].lower()
    return any(m in low for m in _CHALLENGE_MARKERS)


def _parse_date(text: str) -> datetime | None:
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_listing(html: str) -> list[dict]:
    """Extract notice rows from the listing HTML."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    rows: list[dict] = []
    for art in tree.css("article.circulars"):
        header = art.css_first("header")
        if header is None:
            continue
        # The title is the header's direct text node (categories and the
        # issuer/number/date sit inside child <div>s).
        title = " ".join(header.text(deep=False).split())
        circular = date_text = ""
        for div in header.css("div"):
            if div.css_first("strong"):  # the meta row: <strong>issuer</strong><span>num</span><span>date</span>
                spans = div.css("span")
                if len(spans) >= 2:
                    circular = spans[0].text().strip()
                    date_text = spans[1].text().strip()
        categories = [
            c.removeprefix("notice_category-").replace("-", " ")
            for c in art.attributes.get("class", "").split()
            if c.startswith("notice_category-")
        ]
        link = art.css_first("footer a")
        pdf_url = (link.attributes.get("href") or "").strip() if link else ""
        if not (title and pdf_url):
            continue
        rows.append({
            "title": title,
            "circular": circular,
            "published_at": _parse_date(date_text),
            "categories": categories,
            "pdf_url": pdf_url,
        })
    return rows


class BoxAdapter(BaseAdapter):
    def fetch(self) -> list[Item]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)

        try:
            html = self._get(self.source.endpoint)
            if _is_challenge(html):
                raise ValueError("BOX listing returned a Cloudflare challenge")
            rows = parse_listing(html)
        except Exception as e:
            logger.warning("box listing failed (%s); falling back to title-only feed", e)
            return self._fallback_feed(now, cutoff)

        items: list[Item] = []
        for row in rows:
            pub_dt = row["published_at"] or now
            if pub_dt < cutoff:
                continue
            items.append(self._build_item(row, pub_dt, now))
        return items

    def _build_item(self, row: dict, pub_dt: datetime, now: datetime) -> Item:
        body = self._pdf_text(row["pdf_url"])
        cats = ", ".join(row["categories"])
        header = " — ".join(p for p in (
            row["title"],
            f"BOX circular {row['circular']}" if row["circular"] else "",
            cats,
        ) if p)
        raw_text = f"{header}\n{body}"[:8000] if body else header
        return Item(
            id=Item.make_id(self.source.id, row["pdf_url"]),
            source_id=self.source.id,
            venue="BOX",
            title=row["title"],
            url=row["pdf_url"],
            published_at=pub_dt,
            first_seen_at=now,
            raw_text=raw_text,
            modality="scrape",
        )

    def _pdf_text(self, url: str) -> str:
        try:
            resp = httpx.get(url, headers=_BROWSER_HEADERS, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            return pdf_to_text(resp.content)
        except Exception as e:  # per-item isolation (SPEC §10)
            logger.warning("box pdf fetch failed for %s: %s", url, e)
            return ""

    def _fallback_feed(self, now: datetime, cutoff: datetime) -> list[Item]:
        feed = feedparser.parse(self._get(_FEED_URL))
        items: list[Item] = []
        for entry in feed.entries:
            url = entry.get("link", "")
            pub = entry.get("published_parsed")
            pub_dt = datetime(*pub[:6], tzinfo=timezone.utc) if pub else now
            if not url or pub_dt < cutoff:
                continue
            title = (entry.get("title") or "").strip()
            items.append(Item(
                id=Item.make_id(self.source.id, url),
                source_id=self.source.id,
                venue="BOX",
                title=title,
                url=url,
                published_at=pub_dt,
                first_seen_at=now,
                raw_text=title,
                modality="scrape",
            ))
        return items

    @staticmethod
    def _get(url: str) -> str:
        resp = httpx.get(url, headers=_BROWSER_HEADERS, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
