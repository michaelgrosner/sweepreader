"""Tests for the controlled tag vocabulary."""
from __future__ import annotations

from sweepreader.tags import ALLOWED_TAGS, TAG_AXES, TAG_AXIS, sanitize_tags


def test_axes_cover_allowed_tags():
    flat = [t for tags in TAG_AXES.values() for t in tags]
    assert set(flat) == ALLOWED_TAGS
    assert len(flat) == len(set(flat))  # no dupes across axes
    assert TAG_AXIS["protocol"] == "Subject"
    assert TAG_AXIS["options"] == "Market"
    assert TAG_AXIS["deadline"] == "Action"


def test_sanitize_normalizes_and_filters():
    raw = ["Protocol", "order_type", "cert window", "OPTIONS", "bogus", "protocol", 42, None]
    assert sanitize_tags(raw) == ["protocol", "order-type", "cert-window", "options"]


def test_sanitize_handles_non_lists():
    assert sanitize_tags(None) == []
    assert sanitize_tags("protocol") == []
    assert sanitize_tags([]) == []
