from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import httpx

from sweepreader.ingest.base import BaseAdapter, _USER_AGENT
from sweepreader.store.models import Item

if TYPE_CHECKING:
    from sweepreader.config import SourceConfig

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 14
_PER_PAGE = 40
_MAX_PAGES = 10

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


def _truncate(text: str, max_chars: int = 8000) -> str:
    return text[:max_chars] if len(text) > max_chars else text


class FederalRegisterAdapter(BaseAdapter):
    BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"

    def fetch(self) -> list[Item]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
        items: list[Item] = []
        page = 1

        with httpx.Client(
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            while page <= _MAX_PAGES:
                params = {
                    "conditions[agencies][]": "securities-and-exchange-commission",
                    "conditions[term]": "self-regulatory",
                    "per_page": str(_PER_PAGE),
                    "order": "newest",
                    "page": str(page),
                    "fields[]": [
                        "document_number",
                        "title",
                        "html_url",
                        "publication_date",
                        "abstract",
                        "full_text_xml_url",
                        "agencies",
                        "docket_ids",
                    ],
                }
                resp = client.get(self.BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

                results = data.get("results", [])
                if not results:
                    break

                stop = False
                for doc in results:
                    pub_str = doc.get("publication_date", "")
                    try:
                        pub_dt = datetime.strptime(pub_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pub_dt = datetime.now(timezone.utc)

                    if pub_dt < cutoff:
                        stop = True
                        break

                    url = doc.get("html_url", "")
                    filing_no = doc.get("document_number", "")
                    title = doc.get("title", "").strip()
                    abstract = doc.get("abstract") or ""

                    # Use filing number for stable ID when available
                    stable_key = filing_no if filing_no else url
                    item_id = Item.make_id(self.source.id, stable_key)

                    venue = _extract_venue(title, filing_no)
                    raw_text = _truncate(f"{title}\n\n{abstract}")

                    item = Item(
                        id=item_id,
                        source_id=self.source.id,
                        venue=venue,
                        title=title,
                        url=url,
                        published_at=pub_dt,
                        first_seen_at=datetime.now(timezone.utc),
                        raw_text=raw_text,
                        modality="api",
                        cluster_id=filing_no if filing_no else None,
                    )
                    items.append(item)

                if stop or len(results) < _PER_PAGE:
                    break
                page += 1

        return items
