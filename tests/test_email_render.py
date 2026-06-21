"""Tests for the email digest renderer."""
from __future__ import annotations

from datetime import datetime, timezone

from sweepreader.config import AppConfig
from sweepreader.render.email_render import render_email
from sweepreader.store import Store, StateStore
from sweepreader.store.models import Item, Classification


def _config() -> AppConfig:
    cfg = AppConfig(
        model="m", suppress_threshold=35, trailing_days=14, profile_prompt="x",
        tier_weights={"A": 1.0, "B": 0.85, "C": 0.55, "D": 0.40, "E": 0.10},
        sources=[], max_age_days=183, page_url="https://example.com/sr/",
    )
    cfg.config_hash = lambda: "h"
    return cfg


def test_email_renders_tag_chips_on_top_items(tmp_path):
    store = Store(tmp_path / "data")
    state = StateStore(tmp_path / "data")
    config = _config()
    now = datetime.now(timezone.utc)

    item = Item(
        id=Item.make_id("src", "https://example.com/spec"),
        source_id="src", venue="CBOE", title="Cboe protocol spec change",
        url="https://example.com/spec", published_at=now, first_seen_at=now,
        raw_text="", modality="rss",
    )
    cls = Classification(
        item_id=item.id, model="m", config_hash="h", classified_at=now,
        relevance=82, tier="A", rationale="r", summary="A summary",
        tags=["protocol", "options", "cert-window"],
    )
    store.append_item(item)
    store.append_classification(cls)

    html = render_email(config, store, state, dry_run=True)

    assert "Cboe protocol spec change" in html       # top item rendered
    assert ">protocol<" in html                        # tag chips present
    assert ">options<" in html
    assert ">cert-window<" in html
    assert "tagchip" in html                           # chip styling hook
