"""Tests for LLM classifier — uses a fake client, never hits the network."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sweepreader.classify.classifier import OpenRouterClient, keyword_fallback, _extract_json
from sweepreader.config import AppConfig
from sweepreader.store.models import Item, Classification


def make_config() -> AppConfig:
    return AppConfig(
        model="anthropic/claude-haiku-4-5",
        suppress_threshold=35,
        trailing_days=14,
        profile_prompt="Director-level engineer, US options market making",
        tier_weights={"A": 1.0, "B": 0.85, "C": 0.55, "D": 0.40, "E": 0.10},
        sources=[],
    )


def make_item(title="Test Spec Update") -> Item:
    return Item(
        id="abc123",
        source_id="cboe_options_tech",
        venue="CBOE",
        title=title,
        url="https://example.com/notice",
        published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        first_seen_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        raw_text="This is a protocol specification update for C2 adding new message types.",
        modality="rss",
    )


def mock_openrouter_response(payload: dict):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload)}}]
    }
    return mock_resp


def test_classify_valid_response():
    config = make_config()
    item = make_item()
    payload = {
        "relevance": 85,
        "tier": "A",
        "venues": ["CBOE"],
        "rationale": "Protocol spec change affects all Cboe options feeds.",
        "summary": "Cboe will add a new message type to the C2 top-of-book feed.",
    }
    with patch("sweepreader.classify.classifier.httpx.post", return_value=mock_openrouter_response(payload)):
        client = OpenRouterClient(api_key="test-key")
        cls = client.classify(item, config)

    assert cls.relevance == 85
    assert cls.tier == "A"
    assert cls.summary is not None
    assert not cls.unclassified
    assert cls.config_hash == config.config_hash()


def test_classify_falls_back_on_bad_json():
    config = make_config()
    item = make_item()

    bad_resp = MagicMock()
    bad_resp.raise_for_status = MagicMock()
    bad_resp.json.return_value = {
        "choices": [{"message": {"content": "not json at all"}}]
    }

    with patch("sweepreader.classify.classifier.httpx.post", return_value=bad_resp):
        client = OpenRouterClient(api_key="test-key")
        cls = client.classify(item, config)

    assert cls.unclassified is True


def test_keyword_fallback_tier_A():
    item = make_item("Protocol specification update for C2 multicast feed")
    config = make_config()
    cls = keyword_fallback(item, config.model, config.config_hash())
    assert cls.tier == "A"
    assert cls.unclassified is True


def test_keyword_fallback_tier_E():
    item = make_item("Trading halt for XYZ Corp")
    item.raw_text = "Trading halt declared for XYZ Corp common stock."
    config = make_config()
    cls = keyword_fallback(item, config.model, config.config_hash())
    assert cls.tier == "E"


def test_extract_json_from_fenced():
    text = '```json\n{"relevance": 70, "tier": "B", "venues": [], "rationale": "x"}\n```'
    data = _extract_json(text)
    assert data["relevance"] == 70


def test_no_api_key_raises():
    import os
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("OPENROUTER_API_KEY", None)
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            OpenRouterClient()
