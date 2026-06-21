"""Content-addressed HTTP fetch cache for scrapers and the seed CLI.

Separates *fetching* from *parsing* (the bronze→silver split): raw responses are
stored gzipped, keyed by request, so a 6-month seed is resumable and re-parsing
costs no network. Lives outside the committed store (default ``.cache/http``,
gitignored) to avoid bloating the repo — the committed item history keeps only
the capped extracted text, per SPEC §5.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from pathlib import Path

import httpx

from sweepreader.ingest.base import _USER_AGENT

logger = logging.getLogger(__name__)


def _key(url: str, params: dict | None) -> str:
    canonical = url + "?" + json.dumps(params or {}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class HttpCache:
    """Disk cache of raw response text. Historical pages are immutable, so
    entries never expire; delete the cache dir to force a refetch."""

    def __init__(self, root: str | Path = ".cache/http"):
        self.root = Path(root)
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.gz"

    def fetch_text(self, url: str, *, params: dict | None = None,
                   headers: dict | None = None, timeout: float = 30.0) -> str:
        path = self._path(_key(url, params))
        if path.exists():
            self.hits += 1
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return f.read()

        self.misses += 1
        resp = httpx.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        text = resp.text
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
        return text

    def fetch_bytes(self, url: str, *, params: dict | None = None,
                    headers: dict | None = None, timeout: float = 30.0) -> bytes:
        """Like fetch_text but for binary responses (e.g. PDFs)."""
        path = self._path(_key(url, params) + ".bin")
        if path.exists():
            self.hits += 1
            with gzip.open(path, "rb") as f:
                return f.read()

        self.misses += 1
        resp = httpx.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.content
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wb") as f:
            f.write(data)
        return data
