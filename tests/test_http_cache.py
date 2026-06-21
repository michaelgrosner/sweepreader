"""Tests for the content-addressed HTTP fetch cache."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from sweepreader.ingest.http_cache import HttpCache


def _resp(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.raise_for_status = MagicMock()
    return r


def test_caches_after_first_fetch(tmp_path):
    cache = HttpCache(root=tmp_path / "http")
    with patch("sweepreader.ingest.http_cache.httpx.get", return_value=_resp("hello")) as get:
        assert cache.fetch_text("https://x.test/a") == "hello"
        assert cache.fetch_text("https://x.test/a") == "hello"  # served from disk
    get.assert_called_once()  # only one network call
    assert (cache.hits, cache.misses) == (1, 1)


def test_distinct_keys_for_params(tmp_path):
    cache = HttpCache(root=tmp_path / "http")
    with patch("sweepreader.ingest.http_cache.httpx.get",
               side_effect=[_resp("p0"), _resp("p1")]) as get:
        assert cache.fetch_text("https://x.test/list", params={"page": 0}) == "p0"
        assert cache.fetch_text("https://x.test/list", params={"page": 1}) == "p1"
    assert get.call_count == 2  # different params -> different cache entries


def test_fetch_bytes_caches_binary(tmp_path):
    cache = HttpCache(root=tmp_path / "http")
    with patch("sweepreader.ingest.http_cache.httpx.get",
               return_value=_resp_bytes(b"%PDF-1.7\x00\x01binary")) as get:
        assert cache.fetch_bytes("https://x.test/a.pdf") == b"%PDF-1.7\x00\x01binary"
        assert cache.fetch_bytes("https://x.test/a.pdf") == b"%PDF-1.7\x00\x01binary"
    get.assert_called_once()  # second read served from disk, bytes intact


def _resp_bytes(content: bytes) -> MagicMock:
    r = MagicMock()
    r.content = content
    r.raise_for_status = MagicMock()
    return r


def test_persists_across_instances(tmp_path):
    with patch("sweepreader.ingest.http_cache.httpx.get", return_value=_resp("v")) as get:
        HttpCache(root=tmp_path / "http").fetch_text("https://x.test/a")
        # A fresh cache over the same dir reuses the stored response.
        assert HttpCache(root=tmp_path / "http").fetch_text("https://x.test/a") == "v"
    get.assert_called_once()
