"""Offline tests for the OPRA notices adapter."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import sweepreader.ingest.opra as opra
from sweepreader.config import SourceConfig
from sweepreader.ingest.opra import OpraAdapter, parse_homepage

ENDPOINT = "https://www.opraplan.com/"
_CDN = "https://cdn.opraplan.com/documents/notices/"


def _row(date_txt: str, title: str, pdf: str) -> str:
    return (f"<tr><td>{date_txt}</td>"
            f'<td><a href="{_CDN}{pdf}">{title}</a></td></tr>')


def _homepage(*rows: str) -> str:
    return "<html><body><table>" + "".join(rows) + "</table></body></html>"


def _source() -> SourceConfig:
    return SourceConfig(id="opra_notices", modality="scrape", parse="opra_notices",
                        default_tier_hint="A", weight=0.9, endpoint=ENDPOINT)


def _resp(text=None, content=None) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.content = content
    r.raise_for_status = MagicMock()
    return r


def test_parse_homepage():
    rows = parse_homepage(_homepage(
        _row("June 15, 2026", "Extension of OPRA Trading Hours", "ext-hours.pdf"),
        _row("January 2, 2020", "Old Notice", "old.pdf"),
        "<tr><td>no link row</td></tr>",  # ignored
    ))
    assert len(rows) == 2
    assert rows[0]["title"] == "Extension of OPRA Trading Hours"
    assert rows[0]["published_at"] == datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert rows[0]["pdf_url"] == _CDN + "ext-hours.pdf"


def test_fetch_builds_rich_item_within_window():
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    html = _homepage(
        _row(today, "Extension of OPRA Trading Hours", "ext.pdf"),
        _row("January 2, 2018", "Ancient Notice", "ancient.pdf"),
    )

    def fake_get(url, *a, **k):
        return _resp(content=b"%PDF-x") if url.endswith(".pdf") else _resp(text=html)

    with patch.object(opra.httpx, "get", side_effect=fake_get), \
         patch.object(opra, "pdf_to_text", return_value="To: Subscribers Subject: Extension body."):
        items = OpraAdapter(_source()).fetch()

    assert len(items) == 1  # ancient one excluded
    it = items[0]
    assert it.venue == "OPRA"
    assert it.modality == "scrape"
    assert it.url == _CDN + "ext.pdf"
    assert "Extension of OPRA Trading Hours" in it.raw_text
    assert "Subject: Extension body." in it.raw_text  # PDF body extracted


def test_seed_first_seen_equals_published():
    html = _homepage(_row("March 3, 2026", "Some Notice", "n.pdf"))

    def fake_get(url, *a, **k):
        return _resp(content=b"%PDF") if url.endswith(".pdf") else _resp(text=html)

    with patch.object(opra.httpx, "get", side_effect=fake_get), \
         patch.object(opra, "pdf_to_text", return_value="body"):
        items = list(opra.iter_seed_items(
            "opra_notices", ENDPOINT,
            stop_before=datetime(2026, 1, 1, tzinfo=timezone.utc)))

    assert len(items) == 1
    assert items[0].first_seen_at == items[0].published_at == datetime(2026, 3, 3, tzinfo=timezone.utc)
