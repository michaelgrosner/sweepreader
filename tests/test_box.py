"""Offline tests for the BOX notices scrape adapter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import sweepreader.ingest.box as box
from sweepreader.config import SourceConfig
from sweepreader.ingest.box import BoxAdapter, parse_listing

ENDPOINT = "https://boxexchange.com/notices"


def _row(circular: str, title: str, mmddyy: str, cats=("regulatory-notices",)) -> str:
    cls = "post-1 circulars type-circulars issuer-box-exchange " + " ".join(
        f"notice_category-{c}" for c in cats)
    cat_spans = "".join(f"<span>{c}</span>" for c in cats)
    pdf = f"https://boxexchange.com/assets/Notice-{circular}-{title.replace(' ', '-')}.pdf"
    return (
        f'<article id="bxex" class="{cls}"><header>'
        f"<div>{cat_spans}</div>"
        f"{title}"
        f'<div><strong>BOX Exchange</strong><span>{circular}</span><span>{mmddyy}</span></div>'
        f'</header><footer><a href="{pdf}" target="_blank">View Document</a></footer></article>'
    )


def _listing(*rows: str) -> str:
    return "<html><body><main>" + "".join(rows) + "</main></body></html>"


def _source() -> SourceConfig:
    return SourceConfig(id="box_notices", modality="scrape", parse="box_notices",
                        default_tier_hint="A", weight=0.9, endpoint=ENDPOINT)


def _resp(text=None, content=None) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.content = content
    r.raise_for_status = MagicMock()
    return r


def test_parse_listing_extracts_fields():
    rows = parse_listing(_listing(
        _row("2026-059", "Weekend Testing Dates", "06/16/26", cats=("announcements",))))
    assert len(rows) == 1
    r = rows[0]
    assert r["circular"] == "2026-059"
    assert r["title"] == "Weekend Testing Dates"
    assert r["categories"] == ["announcements"]
    assert r["published_at"] == datetime(2026, 6, 16, tzinfo=timezone.utc)
    assert r["pdf_url"].endswith("Notice-2026-059-Weekend-Testing-Dates.pdf")


def test_is_challenge():
    assert box._is_challenge("<html>Attention Required! | Cloudflare</html>")
    assert not box._is_challenge("<html><article class='circulars'>real</article></html>")


def test_fetch_builds_rich_item_from_pdf():
    today = datetime.now(timezone.utc).strftime("%m/%d/%y")
    listing = _listing(
        _row("2026-059", "Weekend Testing Dates", today),
        _row("2000-001", "Ancient Notice", "01/02/00"),  # far older than lookback
    )

    def fake_get(url, *a, **k):
        return _resp(content=b"%PDF-fake") if url.endswith(".pdf") else _resp(text=listing)

    with patch.object(box.httpx, "get", side_effect=fake_get), \
         patch.object(box, "pdf_to_text", return_value="TO: Participants SUBJECT: Weekend Testing body."):
        items = BoxAdapter(_source()).fetch()

    assert len(items) == 1  # ancient one filtered out
    it = items[0]
    assert it.venue == "BOX"
    assert it.modality == "scrape"
    assert it.url.endswith("Notice-2026-059-Weekend-Testing-Dates.pdf")
    assert "BOX circular 2026-059" in it.raw_text
    assert "SUBJECT: Weekend Testing" in it.raw_text  # PDF body extracted


def test_fetch_falls_back_to_feed_on_challenge():
    feed = (
        '<?xml version="1.0"?>\n<rss version="2.0"><channel><title>BOX</title>'
        "<item><title>Feed Title Notice</title>"
        "<link>https://boxexchange.com/notices/feed-title-notice/</link>"
        f"<pubDate>{_rfc822_now()}</pubDate></item></channel></rss>"
    )

    def fake_get(url, *a, **k):
        if "feed" in url:
            return _resp(text=feed)
        return _resp(text="<html>Just a moment... challenge-platform</html>")  # Cloudflare

    with patch.object(box.httpx, "get", side_effect=fake_get):
        adapter = BoxAdapter(_source())
        items = adapter.fetch()

    assert len(items) == 1
    assert items[0].title == "Feed Title Notice"
    assert items[0].venue == "BOX"
    assert adapter.warning is not None
    assert "Cloudflare block" in adapter.warning


def _rfc822_now() -> str:
    from email.utils import format_datetime
    return format_datetime(datetime.now(timezone.utc))


def test_fetch_falls_back_to_feed_and_fails():
    def fake_get(url, *a, **k):
        # both listing and feed raise 403 HTTPStatusError
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = box.httpx.HTTPStatusError(
            "403 Forbidden", request=MagicMock(), response=mock_resp
        )
        raise mock_resp.raise_for_status.side_effect

    with patch.object(box.httpx, "get", side_effect=fake_get):
        adapter = BoxAdapter(_source())
        items = adapter.fetch()

    assert len(items) == 0
    assert adapter.warning is not None
    assert "Both listing and fallback feed failed" in adapter.warning
