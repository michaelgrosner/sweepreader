"""Golden-file test for the page renderer."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from sweepreader.config import AppConfig
from sweepreader.render.page import render_page
from sweepreader.store import Store, StateStore
from sweepreader.store.models import Item, Classification


def make_config() -> AppConfig:
    return AppConfig(
        model="anthropic/claude-haiku-4-5",
        suppress_threshold=35,
        trailing_days=14,
        profile_prompt="Director-level engineer",
        tier_weights={"A": 1.0, "B": 0.85, "C": 0.55, "D": 0.40, "E": 0.10},
        sources=[],
        max_age_days=183,
    )


def seed_store(store: Store, config: AppConfig) -> tuple[Item, Classification]:
    now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    item = Item(
        id=Item.make_id("cboe_options_tech", "https://example.com/spec-update"),
        source_id="cboe_options_tech",
        venue="CBOE",
        title="Cboe C2 Spec Update: New Message Type for Complex Orders",
        url="https://example.com/spec-update",
        published_at=now,
        first_seen_at=now,
        raw_text="C2 will add a new message type for complex order book changes.",
        modality="rss",
    )
    cls = Classification(
        item_id=item.id,
        model=config.model,
        config_hash=config.config_hash(),
        classified_at=now,
        relevance=82,
        tier="A",
        rationale="Protocol spec change affects all Cboe options feeds.",
        summary="Cboe will update the C2 feed to add a new complex-order message type.",
    )
    store.append_item(item)
    store.append_classification(cls)
    return item, cls


@pytest.fixture
def tmp_dirs(tmp_path):
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    data_dir.mkdir()
    docs_dir.mkdir()
    return data_dir, docs_dir


def test_render_page_produces_html(tmp_dirs, monkeypatch):
    data_dir, docs_dir = tmp_dirs
    monkeypatch.chdir(tmp_dirs[1].parent)

    store = Store(data_dir)
    state = StateStore(data_dir)
    config = make_config()

    item, cls = seed_store(store, config)

    # Patch docs dir
    import sweepreader.render.page as page_mod
    monkeypatch.setattr(page_mod, "_DOCS_DIR", docs_dir)

    render_page(config, store, state)

    index = docs_dir / "index.html"
    assert index.exists()
    content = index.read_text()
    assert "SweepReader" in content
    assert "CBOE" in content
    assert "C2 Spec Update" in content
    assert "Cboe will update" in content


def test_render_page_ranked_order(tmp_dirs, monkeypatch):
    data_dir, docs_dir = tmp_dirs
    store = Store(data_dir)
    state = StateStore(data_dir)
    config = make_config()

    now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    # High-relevance D
    item_d = Item(
        id=Item.make_id("src", "https://example.com/d"),
        source_id="src", venue="CBOE", title="High Relevance D Item",
        url="https://example.com/d", published_at=now, first_seen_at=now,
        raw_text="", modality="rss",
    )
    cls_d = Classification(
        item_id=item_d.id, model="m", config_hash="h",
        classified_at=now, relevance=95, tier="D",
        rationale="r", summary="D summary",
    )
    # Low-relevance B
    item_b = Item(
        id=Item.make_id("src", "https://example.com/b"),
        source_id="src", venue="NYSE", title="Low Relevance B Item",
        url="https://example.com/b", published_at=now, first_seen_at=now,
        raw_text="", modality="rss",
    )
    cls_b = Classification(
        item_id=item_b.id, model="m", config_hash="h",
        classified_at=now, relevance=40, tier="B",
        rationale="r", summary="B summary",
    )

    store.append_item(item_d)
    store.append_item(item_b)
    store.append_classification(cls_d)
    store.append_classification(cls_b)

    import sweepreader.render.page as page_mod
    monkeypatch.setattr(page_mod, "_DOCS_DIR", docs_dir)

    # Patch config_hash to match stored classifications
    config._stored_hash = "h"
    original_hash = config.config_hash
    config.config_hash = lambda: "h"

    render_page(config, store, state)
    content = (docs_dir / "index.html").read_text()

    d_pos = content.find("High Relevance D")
    b_pos = content.find("Low Relevance B")
    assert d_pos < b_pos, "High-relevance D item should appear before low-relevance B"


def test_suppressed_not_in_main_body(tmp_dirs, monkeypatch):
    data_dir, docs_dir = tmp_dirs
    store = Store(data_dir)
    state = StateStore(data_dir)
    config = make_config()

    now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    item = Item(
        id=Item.make_id("src", "https://example.com/halt"),
        source_id="src", venue="NASDAQ", title="Trading Halt: XYZ Corp",
        url="https://example.com/halt", published_at=now, first_seen_at=now,
        raw_text="Halt declared", modality="rss",
    )
    cls = Classification(
        item_id=item.id, model="m", config_hash="h2",
        classified_at=now, relevance=10, tier="E",
        rationale="routine halt", summary=None,
    )
    store.append_item(item)
    store.append_classification(cls)

    import sweepreader.render.page as page_mod
    monkeypatch.setattr(page_mod, "_DOCS_DIR", docs_dir)
    config.config_hash = lambda: "h2"

    render_page(config, store, state)
    content = (docs_dir / "index.html").read_text()

    # Item should appear in suppressed section, not in cards
    assert "Trading Halt: XYZ Corp" in content
    assert "suppressed" in content.lower()
