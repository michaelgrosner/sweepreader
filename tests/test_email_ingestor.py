"""Offline tests for EmailIngestor using a fixture .eml file."""
from __future__ import annotations

import email
import imaplib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sweepreader.config import SourceConfig
from sweepreader.ingest.email_ingestor import EmailIngestor, _html_to_text, _extract_text
from sweepreader.store.store import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def email_source() -> SourceConfig:
    return SourceConfig(
        id="email_miax",
        modality="email",
        parse="email_html_or_pdf",
        default_tier_hint="A",
        weight=0.95,
        address="you+miax@gmail.com",
    )


def load_eml(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def mock_imap(eml_bytes: bytes, uid: int = 42):
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    conn.login.return_value = ("OK", [])
    conn.select.return_value = ("OK", [b"1"])
    conn.uid.side_effect = [
        ("OK", [str(uid).encode()]),  # SEARCH
        ("OK", [(b"42 (RFC822 {" + str(len(eml_bytes)).encode() + b"})", eml_bytes)]),  # FETCH
    ]
    return conn


def test_email_ingestor_parses_eml():
    eml = load_eml("miax_alert.eml")
    source = email_source()

    with tempfile.TemporaryDirectory() as d:
        state = StateStore(d)
        ingestor = EmailIngestor(source, state)

        mock_conn = mock_imap(eml)
        with patch("sweepreader.ingest.email_ingestor.imaplib.IMAP4_SSL", return_value=mock_conn):
            with patch.dict("os.environ", {
                "IMAP_USER": "you@gmail.com",
                "IMAP_PASSWORD": "testpass",
            }):
                items = ingestor.fetch()

    assert len(items) == 1
    item = items[0]
    assert item.source_id == "email_miax"
    assert item.modality == "email"
    assert "connectivity" in item.title.lower() or "MIAX" in item.title
    assert "connectivity" in item.raw_text.lower()


def test_email_ingestor_advances_watermark():
    eml = load_eml("miax_alert.eml")
    source = email_source()

    with tempfile.TemporaryDirectory() as d:
        state = StateStore(d)
        ingestor = EmailIngestor(source, state)

        assert ingestor._get_watermark() == 0

        mock_conn = mock_imap(eml, uid=55)
        with patch("sweepreader.ingest.email_ingestor.imaplib.IMAP4_SSL", return_value=mock_conn):
            with patch.dict("os.environ", {
                "IMAP_USER": "you@gmail.com",
                "IMAP_PASSWORD": "testpass",
            }):
                items = ingestor.fetch()

        assert ingestor._get_watermark() == 55


def test_email_ingestor_skips_without_credentials():
    source = email_source()
    ingestor = EmailIngestor(source)
    env_without_creds = {k: v for k, v in __import__("os").environ.items()
                         if k not in ("IMAP_USER", "IMAP_PASSWORD")}
    with patch.dict("os.environ", env_without_creds, clear=True):
        items = ingestor.fetch()
    assert items == []


def test_html_to_text():
    html = "<html><body><h1>Alert</h1><p>Test <b>connectivity</b> window.</p><script>bad()</script></body></html>"
    text = _html_to_text(html)
    assert "Alert" in text
    assert "connectivity" in text
    assert "bad()" not in text
