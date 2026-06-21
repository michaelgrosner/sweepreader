from datetime import datetime, timezone, timedelta

import pytest

from sweepreader.config import AppConfig
from sweepreader.score import compute_score, is_suppressed, rank_items, recency_decay
from sweepreader.store.models import Item, Classification


def make_config() -> AppConfig:
    return AppConfig(
        model="m",
        suppress_threshold=35,
        trailing_days=14,
        profile_prompt="test",
        tier_weights={"A": 1.0, "B": 0.85, "C": 0.55, "D": 0.40, "E": 0.10},
        sources=[],
        max_age_days=183,
    )


def make_item(iid: str, pub_days_ago: int = 0) -> Item:
    now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    pub = now - timedelta(days=pub_days_ago)
    return Item(
        id=iid,
        source_id="s",
        venue="X",
        title="T",
        url=f"https://example.com/{iid}",
        published_at=pub,
        first_seen_at=pub,
        raw_text="",
        modality="rss",
    )


def make_cls(item_id: str, relevance: int, tier: str) -> Classification:
    return Classification(
        item_id=item_id,
        model="m",
        config_hash="h",
        classified_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        relevance=relevance,
        tier=tier,
        rationale="",
        summary="s",
    )


def test_high_relevance_D_outranks_low_relevance_B():
    config = make_config()
    as_of = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

    item_d = make_item("d", pub_days_ago=0)
    item_b = make_item("b", pub_days_ago=0)
    cls_d = make_cls("d", relevance=95, tier="D")
    cls_b = make_cls("b", relevance=40, tier="B")

    score_d = compute_score(item_d, cls_d, config, as_of)
    score_b = compute_score(item_b, cls_b, config, as_of)

    # 95 * 0.40 = 38 vs 40 * 0.85 = 34
    assert score_d > score_b


def test_tier_E_lands_in_suppressed():
    config = make_config()
    as_of = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

    item_e = make_item("e")
    cls_e = make_cls("e", relevance=80, tier="E")

    assert is_suppressed(cls_e, config)

    visible, suppressed = rank_items([item_e], {"e": cls_e}, config, as_of)
    assert len(visible) == 0
    assert len(suppressed) == 1


def test_below_threshold_suppressed():
    config = make_config()
    as_of = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

    item = make_item("x")
    cls = make_cls("x", relevance=20, tier="B")

    assert is_suppressed(cls, config)
    visible, suppressed = rank_items([item], {"x": cls}, config, as_of)
    assert len(visible) == 0
    assert len(suppressed) == 1


def test_recency_decay_at_zero_days():
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    assert abs(recency_decay(now, now) - 1.0) < 1e-9


def test_recency_decay_at_half_life():
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    week_ago = now - timedelta(days=7)
    decay = recency_decay(week_ago, now)
    assert abs(decay - 0.5) < 0.01


def test_rank_items_sorted_desc():
    config = make_config()
    as_of = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

    items = [make_item("a"), make_item("b"), make_item("c")]
    clss = {
        "a": make_cls("a", 50, "B"),
        "b": make_cls("b", 90, "A"),
        "c": make_cls("c", 60, "B"),
    }
    visible, _ = rank_items(items, clss, config, as_of)
    scores = [s for _, _, s in visible]
    assert scores == sorted(scores, reverse=True)
    assert visible[0][0].id == "b"
