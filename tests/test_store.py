import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sweepreader.store.models import Item, Classification
from sweepreader.store.store import Store


def make_item(suffix: str = "a", source_id: str = "test_src", days_ago: int = 0) -> Item:
    now = datetime(2026, 6, 20, 12, 0, 0) - timedelta(days=days_ago)
    url = f"https://example.com/{suffix}"
    return Item(
        id=Item.make_id(source_id, url),
        source_id=source_id,
        venue="TEST",
        title=f"Test item {suffix}",
        url=url,
        published_at=now,
        first_seen_at=now,
        raw_text="Some raw text",
        modality="rss",
    )


def make_cls(item: Item, model: str = "m", config_hash: str = "abc123") -> Classification:
    return Classification(
        item_id=item.id,
        model=model,
        config_hash=config_hash,
        classified_at=item.first_seen_at,
        relevance=70,
        tier="B",
        rationale="Test",
        summary="Test summary",
    )


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield Store(d)


def test_append_item_dedup(store):
    item = make_item("x")
    assert store.append_item(item) is True
    assert store.append_item(item) is False


def test_append_does_not_touch_prior_shard(store):
    jan_item = make_item("jan")
    jan_item.first_seen_at = datetime(2026, 1, 15, 0, 0, 0)
    jan_item.published_at = jan_item.first_seen_at
    store.append_item(jan_item)

    jun_item = make_item("jun")
    store.append_item(jun_item)

    jan_shard = list(store._items_dir.glob("2026-01.jsonl"))
    jun_shard = list(store._items_dir.glob("2026-06.jsonl"))
    assert len(jan_shard) == 1
    assert len(jun_shard) == 1

    # Add another item in June - Jan shard must be untouched
    mtime_jan_before = jan_shard[0].stat().st_mtime
    store.append_item(make_item("jun2"))
    mtime_jan_after = jan_shard[0].stat().st_mtime
    assert mtime_jan_before == mtime_jan_after


def test_classification_dedup(store):
    item = make_item("a")
    store.append_item(item)
    cls = make_cls(item)
    assert store.append_classification(cls) is True
    assert store.append_classification(cls) is False


def test_has_classification(store):
    item = make_item("b")
    store.append_item(item)
    assert not store.has_classification(item.id, "m", "abc123")
    cls = make_cls(item)
    store.append_classification(cls)
    assert store.has_classification(item.id, "m", "abc123")


def test_items_since(store):
    now = datetime(2026, 6, 20, 12, 0, 0)
    old = make_item("old", days_ago=5)
    new = make_item("new", days_ago=0)
    store.append_item(old)
    store.append_item(new)

    cutoff = now - timedelta(days=2)
    results = store.items_since(cutoff)
    ids = {i.id for i in results}
    assert new.id in ids
    assert old.id not in ids


def test_raw_text_truncated(store):
    item = make_item("big")
    item.raw_text = "x" * 10_000
    store.append_item(item)
    store2 = Store(store._data)
    items = store2.items_since(datetime(2026, 1, 1))
    assert len(items[0].raw_text) <= 8000


def test_store_survives_reload(store):
    item = make_item("reload")
    store.append_item(item)
    cls = make_cls(item)
    store.append_classification(cls)

    store2 = Store(store._data)
    assert store2.has_classification(item.id, "m", "abc123")
    assert item.id in store2._known_item_ids
