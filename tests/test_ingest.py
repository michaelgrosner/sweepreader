"""Offline tests for ingest adapters using fixture data."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sweepreader.config import SourceConfig
from sweepreader.ingest.rss import RssAdapter
from sweepreader.ingest.federal_register import FederalRegisterAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def rss_source(source_id="cboe_options_tech") -> SourceConfig:
    return SourceConfig(
        id=source_id,
        modality="rss",
        parse="rss_generic",
        default_tier_hint="A",
        weight=1.0,
        endpoint="https://www.cboe.com/us/options/support/technical/rss",
    )


def fr_source() -> SourceConfig:
    return SourceConfig(
        id="fed_register_sro",
        modality="api",
        parse="federal_register",
        default_tier_hint="D",
        weight=1.0,
        endpoint="https://www.federalregister.gov/api/v1/documents.json",
    )


def test_rss_adapter_parses_fixture():
    xml = (FIXTURES / "cboe_rss.xml").read_text()
    mock_resp = MagicMock()
    mock_resp.text = xml
    mock_resp.raise_for_status = MagicMock()

    with patch("sweepreader.ingest.rss.httpx.get", return_value=mock_resp):
        items = RssAdapter(rss_source()).fetch()

    assert len(items) == 2
    assert items[0].venue == "CBOE"
    assert "C2" in items[0].title
    assert items[0].url == "https://www.cboe.com/us/options/support/technical/spec-update-c2-3-14"
    assert items[0].modality == "rss"


def test_rss_adapter_stable_ids():
    xml = (FIXTURES / "cboe_rss.xml").read_text()
    mock_resp = MagicMock()
    mock_resp.text = xml
    mock_resp.raise_for_status = MagicMock()

    with patch("sweepreader.ingest.rss.httpx.get", return_value=mock_resp):
        items1 = RssAdapter(rss_source()).fetch()
    with patch("sweepreader.ingest.rss.httpx.get", return_value=mock_resp):
        items2 = RssAdapter(rss_source()).fetch()

    assert items1[0].id == items2[0].id
    assert items1[1].id == items2[1].id


def test_federal_register_adapter_parses_fixture():
    payload = json.loads((FIXTURES / "federal_register_response.json").read_text())
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    # The fixture has 2 results (< per_page), so pagination stops after page 1.
    with patch("sweepreader.ingest.federal_register.httpx.get", return_value=mock_resp):
        items = FederalRegisterAdapter(fr_source()).fetch()

    assert len(items) == 2
    memx_item = next(i for i in items if "MEMX" in i.title)
    assert memx_item.venue == "MEMX"
    assert memx_item.cluster_id == "2026-13245"

    cboe_item = next(i for i in items if "Cboe" in i.title)
    assert cboe_item.venue == "CBOE"


def test_federal_register_seed_first_seen_is_published():
    from datetime import datetime, timezone, timedelta
    from sweepreader.ingest import federal_register as fr
    payload = json.loads((FIXTURES / "federal_register_response.json").read_text())
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    with patch("sweepreader.ingest.federal_register.httpx.get", return_value=mock_resp):
        items = list(fr.iter_seed_items(
            "fed_register_sro",
            stop_before=datetime.now(timezone.utc) - timedelta(days=183)))

    assert len(items) == 2
    for it in items:  # historical reconstruction: seen-at == published-at
        assert it.first_seen_at == it.published_at


def test_federal_register_stable_ids():
    payload = json.loads((FIXTURES / "federal_register_response.json").read_text())
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()
    def run():
        with patch("sweepreader.ingest.federal_register.httpx.get", return_value=mock_resp):
            return FederalRegisterAdapter(fr_source()).fetch()

    ids1 = [i.id for i in run()]
    ids2 = [i.id for i in run()]
    assert ids1 == ids2
