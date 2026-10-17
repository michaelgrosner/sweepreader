"""Offline tests for ingest adapters using fixture data."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from sweepreader.config import SourceConfig
from sweepreader.ingest.federal_register import FederalRegisterAdapter
from sweepreader.ingest.rss import RssAdapter
from sweepreader.store.models import Item
from tests.conftest import FIXTURE_NOW

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
        items = RssAdapter(rss_source(), now=FIXTURE_NOW).fetch()

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
        items1 = RssAdapter(rss_source(), now=FIXTURE_NOW).fetch()
    with patch("sweepreader.ingest.rss.httpx.get", return_value=mock_resp):
        items2 = RssAdapter(rss_source(), now=FIXTURE_NOW).fetch()

    assert items1[0].id == items2[0].id
    assert items1[1].id == items2[1].id


def test_federal_register_adapter_parses_fixture():
    payload = json.loads((FIXTURES / "federal_register_response.json").read_text())
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    # The fixture has 2 results (< per_page), so pagination stops after page 1.
    with patch("sweepreader.ingest.federal_register.httpx.get", return_value=mock_resp):
        items = FederalRegisterAdapter(fr_source(), now=FIXTURE_NOW).fetch()

    assert len(items) == 2
    memx_item = next(i for i in items if "MEMX" in i.title)
    assert memx_item.venue == "MEMX"
    assert memx_item.cluster_id == "2026-13245"

    cboe_item = next(i for i in items if "Cboe" in i.title)
    assert cboe_item.venue == "CBOE"


def test_federal_register_seed_first_seen_is_published():
    from datetime import datetime, timedelta, timezone

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
            return FederalRegisterAdapter(fr_source(), now=FIXTURE_NOW).fetch()

    ids1 = [i.id for i in run()]
    ids2 = [i.id for i in run()]
    assert ids1 == ids2


def _fr_payload_with_body_urls() -> dict:
    payload = json.loads((FIXTURES / "federal_register_response.json").read_text())
    for doc in payload["results"]:
        doc["abstract"] = None  # the live API's usual case
        doc["raw_text_url"] = f"https://www.federalregister.gov/documents/full_text/text/{doc['document_number']}.txt"
    return payload


def _fr_get(payload: dict, body: str | Exception):
    """httpx.get stand-in: the listing for the API URL, `body` for document URLs."""
    def get(url, **_kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if url.endswith(".txt"):
            if isinstance(body, Exception):
                raise body
            resp.text = body
        else:
            resp.json.return_value = payload
        return resp
    return get


def test_federal_register_body_starts_at_purpose():
    from sweepreader.ingest.federal_register import _body_text
    text = _body_text((FIXTURES / "federal_register_doc.txt").read_text())
    assert text.startswith("1. Purpose PHLX proposes to amend its Pricing Schedule")
    assert "15 U.S.C. 78s(b)(1)" not in text  # footnote block dropped
    assert "Pursuant to Section 19(b)(1)" not in text  # boilerplate preamble dropped
    assert "\\3\\" not in text  # inline footnote markers dropped


def test_federal_register_fetches_body_only_for_unsettled_items():
    payload = _fr_payload_with_body_urls()
    body = (FIXTURES / "federal_register_doc.txt").read_text()
    settled_id = Item.make_id("fed_register_sro", payload["results"][0]["document_number"])
    get = MagicMock(side_effect=_fr_get(payload, body))
    with patch("sweepreader.ingest.federal_register.httpx.get", get):
        items = FederalRegisterAdapter(fr_source(), now=FIXTURE_NOW,
                                       is_settled={settled_id}.__contains__).fetch()

    by_id = {i.id: i for i in items}
    assert "1. Purpose" not in by_id[settled_id].raw_text
    other = next(i for i in items if i.id != settled_id)
    assert "1. Purpose PHLX proposes" in other.raw_text
    assert other.raw_text.startswith(other.title)
    assert sum(1 for c in get.call_args_list if c.args[0].endswith(".txt")) == 1


def test_federal_register_body_failure_degrades_to_title():
    payload = _fr_payload_with_body_urls()
    with patch("sweepreader.ingest.federal_register.httpx.get",
               side_effect=_fr_get(payload, RuntimeError("boom"))):
        adapter = FederalRegisterAdapter(fr_source(), now=FIXTURE_NOW)
        items = adapter.fetch()

    assert len(items) == 2
    assert all(i.raw_text == i.title for i in items)
    assert adapter.warning is not None and "body fetch" in adapter.warning
