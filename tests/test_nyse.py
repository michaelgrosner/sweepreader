"""Offline tests for the NYSE Trader Updates adapter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import sweepreader.ingest.nyse as nyse
from sweepreader.config import SourceConfig
from sweepreader.ingest.nyse import NyseAdapter, notification_to_item


def _now_ms(days_ago: float = 0) -> int:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return int(dt.timestamp() * 1000)


def _notif(nid: int, days_ago: float, subject: str, body: str, markets, services) -> dict:
    return {
        "id": nid,
        "publishedDate": _now_ms(days_ago),
        "subject": subject,
        "body": body,
        "marketLinks": markets,
        "serviceLinks": services,
        "severityLinks": [],
        "childNotifications": [],
    }


def _source() -> SourceConfig:
    return SourceConfig(
        id="nyse_trader_updates", modality="api", parse="nyse_notifications",
        default_tier_hint="A", weight=0.95,
        endpoint="https://www.nyse.com/api/notifications/public/system/1/summaries/filter",
    )


def test_notification_to_item_fields():
    n = _notif(
        110000957686, 1.0,
        "NYSE Options: Quarterly Bid/Ask Differentials",
        '<p>NYSE American and NYSE Arca will maintain '
        '<a href="x.xlsx">modified differentials</a>.</p><script>junk()</script>',
        ["NYSE AMERICAN OPTIONS", "NYSE ARCA OPTIONS"],
        ["Trading", "Market Data"],
    )
    seen = datetime(2026, 6, 20, tzinfo=timezone.utc)
    it = notification_to_item(n, "nyse_trader_updates", first_seen_at=seen)

    assert it.url == "https://www.nyse.com/trader-update/history#110000957686"
    assert it.id == nyse.Item.make_id("nyse_trader_updates", it.url)
    assert it.venue == "NYSE American Options / NYSE Arca Options"
    assert it.title == "NYSE Options: Quarterly Bid/Ask Differentials"
    assert it.modality == "api"
    assert it.first_seen_at == seen
    # body HTML stripped (no tags, no script), tags line present.
    assert "modified differentials" in it.raw_text
    assert "<p>" not in it.raw_text and "junk()" not in it.raw_text
    assert "Markets: NYSE AMERICAN OPTIONS, NYSE ARCA OPTIONS" in it.raw_text


def test_venue_defaults_to_nyse_when_no_markets():
    n = _notif(1, 0.5, "x", "<p>y</p>", [], [])
    it = notification_to_item(n, "nyse_trader_updates", first_seen_at=datetime.now(timezone.utc))
    assert it.venue == "NYSE"


def _mock_pages(*pages: tuple[list[dict], int]):
    """Return a fake httpx.get whose JSON reflects the requested pageNumber."""
    def fake_get(url, params=None, **kwargs):
        page = params["pageNumber"]
        data, total = pages[page] if page < len(pages) else ([], pages[-1][1])
        resp = MagicMock()
        resp.json.return_value = {"data": data, "totalCount": total}
        resp.raise_for_status = MagicMock()
        return resp
    return fake_get


def test_iter_stops_before_cutoff():
    page0 = [_notif(3, 1, "recent", "b", ["NYSE"], []),
             _notif(2, 5, "mid", "b", ["NYSE"], []),
             _notif(1, 40, "old", "b", ["NYSE"], [])]
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    with patch.object(nyse.httpx, "get", side_effect=_mock_pages((page0, 3))):
        got = list(nyse.iter_notifications(stop_before=cutoff))
    assert [n["id"] for n in got] == [3, 2]  # 40-days-old item excluded


def test_iter_paginates_until_total():
    p0 = ([_notif(i, 1, f"s{i}", "b", ["NYSE"], []) for i in range(2)], 3)
    p1 = ([_notif(9, 2, "s9", "b", ["NYSE"], [])], 3)
    with patch.object(nyse.httpx, "get", side_effect=_mock_pages(p0, p1)):
        got = list(nyse.iter_notifications(page_size=2))
    assert [n["id"] for n in got] == [0, 1, 9]


def test_adapter_fetch_builds_items_within_window():
    page0 = [_notif(3, 1, "recent options change", "<p>body</p>", ["NYSE ARCA OPTIONS"], ["Trading"]),
             _notif(1, 99, "ancient", "<p>x</p>", ["NYSE"], [])]
    with patch.object(nyse.httpx, "get", side_effect=_mock_pages((page0, 2))):
        items = NyseAdapter(_source()).fetch()
    assert len(items) == 1
    assert items[0].title == "recent options change"
    assert items[0].first_seen_at.tzinfo is not None
