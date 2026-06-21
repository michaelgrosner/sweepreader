"""Enrichment for Cboe Titanium technical-spec pages.

Cboe migrated its technical specifications from PDFs to a Next.js single-page
app. The RSS feeds now link to spec pages such as

    https://www.cboe.com/document/tech-spec/content/technical-specifications/<slug>/<section>

whose RSS entries carry an empty title and no body, so the classifier would be
blind. Every spec, however, exposes a sibling ``/revision-history`` page whose
change-log table is embedded (escaped) in the server-rendered React flight
payload -- no JavaScript execution required to read it. We map the feed URL to
that revision-history page, reassemble the flight chunks, and surface the latest
change-log row. That single row is small, dated, and is exactly the "what
changed" signal we want to hand the LLM.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser

from sweepreader.ingest.base import _USER_AGENT

logger = logging.getLogger(__name__)

# A Cboe tech-spec page, capturing the document slug. Both the SPA content tree
# (.../content/...) and the canonical document URL (.../document/...) are covered.
_SPEC_URL_RE = re.compile(
    r"cboe\.com/document/tech-spec/(?:content|document)/technical-specifications/([^/?#]+)"
)
# Next.js streams the page as a sequence of self.__next_f.push([1,"<chunk>"])
# calls; the HTML we want is split across chunks, so we concatenate the decoded
# string args back into one continuous flight stream before handing it to a parser.
_FLIGHT_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y")


@dataclass(frozen=True)
class CboeEnrichment:
    spec_title: str | None
    version: str
    revision_date: datetime | None
    description: str

    def raw_text(self) -> str:
        date_str = self.revision_date.strftime("%Y-%m-%d") if self.revision_date else "unknown date"
        header = self.spec_title or "Cboe technical specification"
        return f"{header}\nLatest revision: v{self.version} ({date_str})\n{self.description}"[:8000]


def spec_slug(url: str) -> str | None:
    """Return the document slug for a Cboe tech-spec URL, else None."""
    m = _SPEC_URL_RE.search(url)
    return m.group(1) if m else None


def revision_history_url(slug: str) -> str:
    return (
        "https://www.cboe.com/document/tech-spec/content/"
        f"technical-specifications/{slug}/revision-history"
    )


def _flight_text(html: str) -> str:
    return "".join(json.loads(f'"{c}"') for c in _FLIGHT_CHUNK_RE.findall(html))


def _cell_text(node) -> str:
    return " ".join(node.text().split())


def _parse_date(value: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_revision_history(html: str) -> CboeEnrichment | None:
    """Extract the latest change-log row from a revision-history page's HTML.

    Returns None when no revision-history table is present (e.g. a spec that
    lacks one), letting the caller fall back to feed-only metadata.
    """
    title_el = HTMLParser(html).css_first("title")
    # "<spec> - Revision History | Cboe" -> "<spec>"
    spec_title = None
    if title_el:
        spec_title = _cell_text(title_el).split(" - Revision History")[0].strip() or None

    table = HTMLParser(_flight_text(html)).css_first("table.rev_history")
    if table is None:
        return None

    # Rows are oldest-first; the last row with data cells is the most recent revision.
    data_rows = [r for r in table.css("tr") if r.css("td")]
    if not data_rows:
        return None

    cells = [_cell_text(c) for c in data_rows[-1].css("td")]
    if len(cells) < 3:
        return None

    version, date_str, description = cells[0], cells[1], cells[2]
    return CboeEnrichment(
        spec_title=spec_title,
        version=version,
        revision_date=_parse_date(date_str),
        description=description,
    )


def enrich(url: str, *, timeout: float = 30.0) -> CboeEnrichment | None:
    """Fetch and parse the revision history for a Cboe tech-spec feed URL.

    Returns None when the URL is not a spec page, the fetch fails, or the page
    has no revision-history table. Network/parse errors are swallowed so a single
    bad item never aborts the source (see SPEC §10 per-item isolation).
    """
    slug = spec_slug(url)
    if not slug:
        return None
    try:
        resp = httpx.get(
            revision_history_url(slug),
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return parse_revision_history(resp.text)
    except Exception as e:
        logger.warning("cboe enrich failed for %s: %s", url, e)
        return None
