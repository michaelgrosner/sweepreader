"""Offline tests for the MIAX alert scrape adapter."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import sweepreader.ingest.miax as miax
from sweepreader.config import SourceConfig
from sweepreader.ingest.miax import MiaxAdapter

# A recent date keeps the fixtures inside the 14-day lookback window.
_RECENT = datetime.now(timezone.utc).strftime("%Y/%m/%d")
_RECENT_DT = datetime.strptime(_RECENT, "%Y/%m/%d").replace(tzinfo=timezone.utc)


def _teaser(path: str, types: str, venue: str, title: str) -> str:
    return (
        f'<div class="views-row"><article about="{path}" '
        'class="node node--type-alert node--view-mode-teaser"><div class="node__content">'
        '<div class="subheading datetime">'
        f'<div class="alert-type-items">{types}</div>'
        f'<div class="alert-exchange-type-items">{venue}</div>'
        '<span class="date">June 17, 2026</span></div>'
        f'<div class="heading-text">{title}</div>'
        '<p>Teaser snippet that gets replaced by the detail body...</p>'
        '</div></article></div>'
    )


def _listing(*teasers: str) -> str:
    return "<html><body><div class='view-content'>" + "".join(teasers) + "</div></body></html>"


_DETAIL = (
    '<html><body><header>site nav junk</header>'
    '<article about="/alert/x" class="node node--type-alert node--view-mode-full">'
    '<div class="node__content"><div class="heading-text">Title</div>'
    '<p>Increased Options Leg Ratio available Monday. '
    'Certification is not required to use this feature in production.</p>'
    '<svg><path d="M0 0"/></svg>'
    '</div></article><footer>footer junk</footer></body></html>'
)


def _source() -> SourceConfig:
    return SourceConfig(
        id="miax_options",
        modality="scrape",
        parse="miax_alerts",
        default_tier_hint="A",
        weight=0.95,
        endpoint="https://www.miaxglobal.com/markets/us-options/all-options-exchange/alerts",
    )


def _fetch(listing: str, detail: str = _DETAIL):
    class R:
        def __init__(self, t): self.text = t
        def raise_for_status(self): pass

    def fake(url, *a, **k):
        return R(listing if url.endswith("/alerts") else detail)

    with patch.object(miax.httpx, "get", side_effect=fake):
        return MiaxAdapter(_source()).fetch()


def test_parses_listing_fields_and_detail_body():
    path = f"/alert/{_RECENT}/miax-sapphire-options-exchange-reminder-enhancements"
    items = _fetch(_listing(_teaser(
        path,
        "Regulatory Alert, Trading Alert",
        "MIAX Sapphire",
        "MIAX Sapphire Options Exchange - Reminder: Enhancements",
    )))
    assert len(items) == 1
    it = items[0]
    assert it.url == "https://www.miaxglobal.com" + path
    assert it.venue == "MIAX Sapphire"
    assert it.title == "MIAX Sapphire Options Exchange - Reminder: Enhancements"
    assert it.published_at == _RECENT_DT
    assert it.modality == "scrape"
    # Header context + detail body, with nav/footer/svg stripped.
    assert "Regulatory Alert, Trading Alert" in it.raw_text
    assert "Certification is not required" in it.raw_text
    assert "site nav junk" not in it.raw_text
    assert "footer junk" not in it.raw_text


def test_dedups_repeated_alert_urls():
    path = f"/alert/{_RECENT}/miax-exchange-group-options-spacex-allocation"
    teaser = _teaser(path, "Technical Alert", "MIAX Pearl", "SpaceX Cloud Allocation")
    items = _fetch(_listing(teaser, teaser))  # same alert featured twice
    assert len(items) == 1


def test_skips_alerts_older_than_lookback():
    old = _teaser("/alert/2020/01/01/ancient-alert", "Trading Alert", "MIAX", "Ancient")
    assert _fetch(_listing(old)) == []


def test_detail_failure_falls_back_to_header():
    path = f"/alert/{_RECENT}/miax-options-exchange-symbol-rebalance"
    listing = _listing(_teaser(path, "Technical Alert", "MIAX Options", "Symbol Rebalance"))

    class R:
        def __init__(self, t): self.text = t
        def raise_for_status(self): pass

    def fake(url, *a, **k):
        if url.endswith("/alerts"):
            return R(listing)
        raise RuntimeError("detail down")

    with patch.object(miax.httpx, "get", side_effect=fake):
        items = MiaxAdapter(_source()).fetch()
    assert len(items) == 1
    assert items[0].raw_text == "MIAX Options — Technical Alert — Symbol Rebalance"


def test_title_fallback_from_slug():
    assert miax._title_from_path("/alert/2026/06/18/miax-options-exchange-symbol-rebalance-2") == \
        "Miax Options Exchange Symbol Rebalance"
