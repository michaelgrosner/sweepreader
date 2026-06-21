"""Shared PDF→text extraction (pypdf).

Release notes / notice bodies live at the front of these documents, so we read
only the first few pages — enough for the classifier, cheap to parse.
"""
from __future__ import annotations

import io
import logging

import pypdf

logger = logging.getLogger(__name__)


def pdf_to_text(data: bytes, *, max_pages: int = 5) -> str:
    """Whitespace-collapsed text from the first `max_pages` of a PDF. Returns ""
    on any parse failure (encrypted, malformed, image-only)."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages[:max_pages]]
        return " ".join(" ".join(parts).split())
    except Exception as e:
        logger.warning("pdf extraction failed: %s", e)
        return ""
