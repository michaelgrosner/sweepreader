"""Offline tests for the historical seed iterators (NYSE + MIAX)."""
from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import sweepreader.ingest.nyse as nyse
import sweepreader.ingest.miax as miax
import sweepreader.cli.seed as seedmod
from sweepreader.config import AppConfig, SourceConfig


def test_seed_clamps_window_to_max_age():
    cfg = AppConfig(
        model="m", suppress_threshold=35, trailing_days=14, profile_prompt="p",
        tier_weights={"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0, "E": 1.0},
        sources=[SourceConfig(id="fed_register_sro", modality="api", parse="federal_register",
                              default_tier_hint="D", weight=1.0)],
        max_age_days=30,
    )
    captured = {}

    def fake_fr(source_id, *, stop_before, cache=None):
        captured["stop_before"] = stop_before
        return iter(())

    args = Namespace(config="x", months=12.0, source="fed_register_sro",
                     no_cache=True, all_bodies=True, body_min_relevance=0)
    with patch.object(seedmod, "load_config", return_value=cfg), \
         patch.object(seedmod, "Store"), \
         patch.object(seedmod.federal_register, "iter_seed_items", side_effect=fake_fr):
        seedmod.cmd_seed(args)

    now = datetime.now(timezone.utc)
    # --months 12 (~365d) is clamped to max_age_days=30, not honored as requested.
    assert now - timedelta(days=40) < captured["stop_before"] < now - timedelta(days=20)


# --- NYSE -------------------------------------------------------------------

def _notif(nid, days_ago, subject="s", body="<p>b</p>", markets=("NYSE",)):
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"id": nid, "publishedDate": int(dt.timestamp() * 1000), "subject": subject,
            "body": body, "marketLinks": list(markets), "serviceLinks": []}


def test_nyse_seed_first_seen_equals_published():
    page0 = [_notif(2, 1), _notif(1, 40)]  # one in window, one older
    def fake_get(url, params=None, **kw):
        r = MagicMock(); r.raise_for_status = MagicMock()
        r.json.return_value = {"data": page0 if params["pageNumber"] == 0 else [], "totalCount": 2}
        return r
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    with patch.object(nyse.httpx, "get", side_effect=fake_get):
        items = list(nyse.iter_seed_items("nyse_trader_updates", stop_before=cutoff))
    assert len(items) == 1
    assert items[0].first_seen_at == items[0].published_at  # historical reconstruction


# --- MIAX -------------------------------------------------------------------

def _teaser(path, title="T", venue="MIAX", types="Technical Alert"):
    return (
        f'<article about="{path}" class="node node--type-alert node--view-mode-teaser">'
        f'<div class="alert-type-items">{types}</div>'
        f'<div class="alert-exchange-type-items">{venue}</div>'
        f'<div class="heading-text">{title}</div></article>'
    )


def _listing(*teasers):
    return "<html><body>" + "".join(teasers) + "</body></html>"


_DETAIL = ('<article class="node node--type-alert node--view-mode-full">'
           '<div class="node__content"><p>Full body text.</p></div></article>')


def test_miax_seed_pages_until_cutoff_and_dates_from_path():
    today = datetime.now(timezone.utc)
    d = lambda days: (today - timedelta(days=days)).strftime("/alert/%Y/%m/%d/slug-")
    page0 = _listing(_teaser(d(1) + "a"), _teaser(d(2) + "b"))
    page1 = _listing(_teaser(d(40) + "c"))  # entirely older than cutoff -> stop

    def fake_get(url, *a, **k):
        r = MagicMock(); r.raise_for_status = MagicMock()
        if "page=0" in url:
            r.text = page0
        elif "page=1" in url:
            r.text = page1
        else:
            r.text = _DETAIL  # detail fetch
        return r

    cutoff = today - timedelta(days=14)
    with patch.object(miax.httpx, "get", side_effect=fake_get), \
         patch.object(miax.time, "sleep", lambda *_: None):
        items = list(miax.iter_seed_items(
            "miax_options", "https://www.miaxglobal.com/markets/x/alerts", stop_before=cutoff))

    assert len(items) == 2  # two recent alerts; the 40-day-old one is excluded
    assert all(it.first_seen_at == it.published_at for it in items)
    assert all("Full body text." in it.raw_text for it in items)
    assert all(it.modality == "scrape" for it in items)


def test_miax_seed_body_gate_skips_noise_detail_fetch():
    today = datetime.now(timezone.utc)
    d = lambda days: (today - timedelta(days=days)).strftime("/alert/%Y/%m/%d/slug-")
    page0 = _listing(
        _teaser(d(1) + "spec", title="Specification update for protocol"),  # relevant -> body
        _teaser(d(2) + "halt", title="Trading halt corporate action"),      # tier E -> teaser-only
    )
    detail_calls: list[str] = []

    def fake_get(url, *a, **k):
        r = MagicMock(); r.raise_for_status = MagicMock()
        if "page=0" in url:
            r.text = page0
        elif "page=" in url:
            r.text = _listing()  # empty subsequent pages
        else:
            detail_calls.append(url)
            r.text = _DETAIL
        return r

    # Gate mirrors the CLI: skip detail for tier-E keyword matches.
    def gate(item):
        from sweepreader.classify.classifier import keyword_fallback
        return keyword_fallback(item, "m", "h").tier != "E"

    cutoff = today - timedelta(days=14)
    with patch.object(miax.httpx, "get", side_effect=fake_get), \
         patch.object(miax.time, "sleep", lambda *_: None):
        items = list(miax.iter_seed_items(
            "miax_options", "https://www.miaxglobal.com/markets/x/alerts",
            stop_before=cutoff, body_gate=gate))

    assert len(items) == 2
    assert len(detail_calls) == 1  # only the relevant alert triggered a detail fetch
    spec = next(i for i in items if "Specification" in i.title)
    halt = next(i for i in items if "halt" in i.title)
    assert "Full body text." in spec.raw_text       # body fetched
    assert "Full body text." not in halt.raw_text   # teaser-only
