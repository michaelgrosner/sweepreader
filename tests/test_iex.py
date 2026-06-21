"""Offline tests for the IEX Trading Alerts adapter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import sweepreader.ingest.iex as iex
from sweepreader.config import SourceConfig
from sweepreader.ingest.iex import IexAdapter, alert_to_item


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _alert(aid, days_ago, title="Alert", content="<p>Body <b>text</b>.</p>",
           venue="Options", category="Informational"):
    return {"id": aid, "title": title, "content": content, "category": category,
            "venue": venue, "alert_id": f"2026-{aid:03d}", "published_at": _iso(days_ago),
            "alert_date": _iso(days_ago)}


def _source() -> SourceConfig:
    return SourceConfig(id="iex_alerts", modality="api", parse="iex_alerts",
                        default_tier_hint="A", weight=0.9,
                        endpoint="https://api.notifications.iex.io/api/v1/public/trading-alerts")


def _mock_pages(*pages):
    """pages: list of (items, total_pages); dispatched by the `page` param (1-indexed)."""
    def fake_get(url, params=None, **kw):
        idx = params["page"] - 1
        items, total = pages[idx] if idx < len(pages) else ([], pages[-1][1])
        r = MagicMock(); r.raise_for_status = MagicMock()
        r.json.return_value = {"items": items, "pages": total, "total": 99, "page": params["page"]}
        return r
    return fake_get


def test_alert_to_item_fields():
    a = _alert(36, 1, title="DEEP+ Testing", content="<p>Launch <b>details</b>.</p><script>x()</script>",
               venue="Options", category="Market Data")
    seen = datetime(2026, 6, 20, tzinfo=timezone.utc)
    it = alert_to_item(a, "iex_alerts", first_seen_at=seen)
    assert it.url == "https://notifications.iex.io/tradingalerts/36"
    assert it.id == iex.Item.make_id("iex_alerts", it.url)
    assert it.venue == "IEX Options"
    assert it.title == "DEEP+ Testing"
    assert it.modality == "api"
    assert it.first_seen_at == seen
    assert "Launch details" in it.raw_text and "x()" not in it.raw_text  # HTML stripped
    assert "Category: Market Data" in it.raw_text and "Alert 2026-036" in it.raw_text


def test_fetch_filters_lookback():
    page1 = [_alert(3, 1), _alert(1, 40)]  # one recent, one older than 14d
    with patch.object(iex.httpx, "get", side_effect=_mock_pages((page1, 1))):
        items = IexAdapter(_source()).fetch()
    assert [i.title for i in items] == ["Alert"]  # the 40-day-old one excluded


def test_iter_paginates_until_last_page():
    p1 = ([_alert(5, 1), _alert(4, 2)], 2)
    p2 = ([_alert(3, 3)], 2)
    with patch.object(iex.httpx, "get", side_effect=_mock_pages(p1, p2)):
        got = list(iex.iter_alerts(limit=2))
    assert [a["id"] for a in got] == [5, 4, 3]


def test_seed_first_seen_equals_published():
    page1 = [_alert(2, 1)]
    with patch.object(iex.httpx, "get", side_effect=_mock_pages((page1, 1))):
        items = list(iex.iter_seed_items("iex_alerts",
                                         stop_before=datetime.now(timezone.utc) - timedelta(days=14)))
    assert len(items) == 1
    assert items[0].first_seen_at == items[0].published_at
