"""Offline tests for Cboe revision-history enrichment."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sweepreader.config import SourceConfig
from sweepreader.ingest import cboe
from sweepreader.ingest.rss import RssAdapter

SPEC_URL = (
    "https://www.cboe.com/document/tech-spec/content/technical-specifications/"
    "cboe-titanium-u.s.-options-boev3-specification/introduction"
)


def _flight_html(table_html: str, *, title: str) -> str:
    """Mimic Cboe's Next.js output: the table HTML lives in the React flight
    stream, JSON-escaped and split across two __next_f.push chunks."""
    escaped = json.dumps(table_html)[1:-1]  # drop surrounding quotes
    mid = len(escaped) // 2
    chunk1, chunk2 = escaped[:mid], escaped[mid:]
    return (
        f"<html><head><title>{title}</title></head><body>"
        '<script>self.__next_f.push([0,""])</script>'
        f'<script>self.__next_f.push([1,"{chunk1}"])</script>'
        f'<script>self.__next_f.push([1,"{chunk2}"])</script>'
        "</body></html>"
    )


_TABLE = (
    '<table class="table rev_history frame-topbot">'
    "<thead><tr><th>Version</th><th>Date</th><th>Description</th></tr></thead>"
    "<tbody>"
    '<tr><td>1.0.0</td><td>02/09/24</td><td>Initial version.</td></tr>'
    '<tr><td>1.1.14</td><td>06/12/26</td>'
    "<td>Updated planned expansion of C1 trading hours to 08/17/26.</td></tr>"
    "</tbody></table>"
)


def _resp(text: str) -> MagicMock:
    m = MagicMock()
    m.text = text
    m.raise_for_status = MagicMock()
    return m


def test_spec_slug_extraction():
    assert cboe.spec_slug(SPEC_URL) == "cboe-titanium-u.s.-options-boev3-specification"
    assert cboe.spec_slug("https://cdn.cboe.com/resources/membership/Foo.pdf") is None


def test_parse_revision_history_picks_latest_row():
    html = _flight_html(_TABLE, title="Cboe Titanium U.S. Options BOEv3 Specification - Revision History | Cboe")
    enr = cboe.parse_revision_history(html)
    assert enr is not None
    assert enr.version == "1.1.14"
    assert enr.revision_date == datetime(2026, 6, 12, tzinfo=timezone.utc)
    assert enr.spec_title == "Cboe Titanium U.S. Options BOEv3 Specification"
    assert "08/17/26" in enr.description
    assert "Latest revision: v1.1.14 (2026-06-12)" in enr.raw_text()


def test_parse_revision_history_no_table_returns_none():
    html = "<html><head><title>Some Spec | Cboe</title></head><body>no table here</body></html>"
    assert cboe.parse_revision_history(html) is None


def test_enrich_skips_non_spec_urls():
    # Non-spec URL -> no network call, returns None.
    with patch("sweepreader.ingest.cboe.httpx.get") as get:
        assert cboe.enrich("https://cdn.cboe.com/resources/membership/Foo.pdf") is None
        get.assert_not_called()


def test_enrich_swallows_fetch_errors():
    with patch("sweepreader.ingest.cboe.httpx.get", side_effect=RuntimeError("boom")):
        assert cboe.enrich(SPEC_URL) is None


def _cboe_feed(url: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        "<title>Cboe</title>"
        f"<item><title></title><link>{url}</link>"
        "<pubDate>Mon, 15 Jun 2026 00:00:00 -0400</pubDate>"
        f"<guid>{url}</guid></item>"
        "</channel></rss>"
    )


def _cboe_source() -> SourceConfig:
    return SourceConfig(
        id="cboe_options_tech",
        modality="rss",
        parse="rss_generic",
        default_tier_hint="A",
        weight=1.0,
        endpoint="https://www.cboe.com/us/options/support/technical/rss",
    )


def test_rss_adapter_enriches_cboe_item():
    feed = _cboe_feed("https://cdn.cboe.com/" + SPEC_URL)  # double-scheme, as the real feed emits
    rev_html = _flight_html(_TABLE, title="Cboe Titanium U.S. Options BOEv3 Specification - Revision History | Cboe")

    # rss.httpx and cboe.httpx are the same module object, so a single patch
    # covers both call sites; dispatch the response by requested URL.
    def fake_get(url, *args, **kwargs):
        return _resp(rev_html if "revision-history" in url else feed)

    with patch("httpx.get", side_effect=fake_get):
        items = RssAdapter(_cboe_source()).fetch()

    assert len(items) == 1
    item = items[0]
    # _clean_url stripped the cdn double-scheme prefix.
    assert item.url == SPEC_URL
    assert item.title == "Cboe Titanium U.S. Options BOEv3 Specification"
    assert "Latest revision: v1.1.14" in item.raw_text
    assert "C1 trading hours" in item.raw_text
