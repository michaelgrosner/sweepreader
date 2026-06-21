from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Iterator

import httpx

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"
_LOOKBACK_DAYS = 14
_PER_PAGE = 40
_MAX_LIVE_PAGES = 10  # the seeder pages without this cap (bounded instead by the date window)

_VENUE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bCBOE\b|\bCboe\b', re.I), "CBOE"),
    (re.compile(r'\bNYSE\b', re.I), "NYSE"),
    (re.compile(r'\bNASDAQ\b|\bNasdaqtrader\b', re.I), "NASDAQ"),
    (re.compile(r'\bMIAX\b', re.I), "MIAX"),
    (re.compile(r'\bBOX\b', re.I), "BOX"),
    (re.compile(r'\bMEMX\b', re.I), "MEMX"),
    (re.compile(r'\bIEX\b', re.I), "IEX"),
    (re.compile(r'\bFINRA\b', re.I), "FINRA"),
    (re.compile(r'\bOCC\b', re.I), "OCC"),
    (re.compile(r'\bCAT\b', re.I), "CAT"),
    (re.compile(r'\bICE\b', re.I), "ICE"),
    (re.compile(r'\bBATS\b|\bBZX\b|\bEDGX\b|\bEDGA\b|\bBYX\b', re.I), "CBOE"),
    (re.compile(r'\bPHLX\b|\bGEMX\b|\bMRX\b|\bNOM\b|\bBX\b', re.I), "NASDAQ"),
    (re.compile(r'\bSR-([A-Z]+)-\d{4}', re.I), None),  # extract from filing number
]

_FILING_RE = re.compile(r'SR-([A-Z]+)-\d{4}-\d+')


def _extract_venue(title: str, filing_number: str | None) -> str:
    if filing_number:
        m = _FILING_RE.search(filing_number)
        if m:
            return m.group(1).upper()
    for pattern, venue in _VENUE_PATTERNS:
        if venue and pattern.search(title):
            return venue
    return "SEC"


def _doc_published_at(doc: dict) -> datetime | None:
    try:
        return datetime.strptime(doc.get("publication_date", ""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _doc_to_item(doc: dict, source_id: str, *, first_seen_at: datetime) -> Item:
    url = doc.get("html_url", "")
    filing_no = doc.get("document_number", "")
    title = doc.get("title", "").strip()
    abstract = doc.get("abstract") or ""
    stable_key = filing_no or url
    return Item(
        id=Item.make_id(source_id, stable_key),
        source_id=source_id,
        venue=_extract_venue(title, filing_no),
        title=title,
        url=url,
        published_at=_doc_published_at(doc) or first_seen_at,
        first_seen_at=first_seen_at,
        raw_text=f"{title}\n\n{abstract}"[:8000],
        modality="api",
        cluster_id=filing_no or None,
    )


def _fetch_page(page: int, *, gte_date: str, timeout: float = 30.0, cache=None) -> list[dict]:
    params = {
        "conditions[agencies][]": "securities-and-exchange-commission",
        "conditions[term]": "self-regulatory",
        "conditions[publication_date][gte]": gte_date,
        "per_page": str(_PER_PAGE),
        "order": "newest",
        "page": str(page),
        "fields[]": [
            "document_number", "title", "html_url", "publication_date",
            "abstract", "full_text_xml_url", "agencies", "docket_ids",
        ],
    }
    headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"}
    if cache is not None:
        payload = json.loads(cache.fetch_text(_BASE_URL, params=params, headers=headers, timeout=timeout))
    else:
        resp = httpx.get(_BASE_URL, params=params, headers=headers, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
    return payload.get("results", [])


def iter_documents(*, stop_before: datetime, max_pages: int | None = None, cache=None) -> Iterator[dict]:
    """Yield SRO documents newest-first back to `stop_before`. The query is also
    bounded server-side by publication_date >= stop_before, so pagination ends
    naturally; `max_pages` caps the live run."""
    gte_date = stop_before.strftime("%Y-%m-%d")
    page = 1
    while max_pages is None or page <= max_pages:
        results = _fetch_page(page, gte_date=gte_date, cache=cache)
        if not results:
            return
        for doc in results:
            pub = _doc_published_at(doc)
            if pub is not None and pub < stop_before:
                return
            yield doc
        if len(results) < _PER_PAGE:
            return
        page += 1


def iter_seed_items(source_id: str, *, stop_before: datetime, cache=None) -> Iterator[Item]:
    """Historical backfill: first_seen = publication date. (Federal Register
    paginates up to ~2000 results per query; a multi-year seed would need
    date-window chunking, but a 6-month window is well within that.)"""
    for doc in iter_documents(stop_before=stop_before, cache=cache):
        yield _doc_to_item(doc, source_id, first_seen_at=_doc_published_at(doc) or stop_before)


class FederalRegisterAdapter(BaseAdapter):
    BASE_URL = _BASE_URL

    def fetch(self) -> list[Item]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_LOOKBACK_DAYS)
        return [
            _doc_to_item(doc, self.source.id, first_seen_at=now)
            for doc in iter_documents(stop_before=cutoff, max_pages=_MAX_LIVE_PAGES)
        ]
