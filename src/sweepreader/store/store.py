from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sweepreader.store.models import Item, Classification

logger = logging.getLogger(__name__)

_MAX_RAW_CHARS = 8000  # ~2k tokens


def _shard_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _get_shards_in_range(directory: Path, start: datetime, end: datetime) -> list[Path]:
    """Return sorted list of monthly shard jsonl paths in directory that cover
    the date range [start, end]."""
    st = start.replace(tzinfo=None) if start.tzinfo else start
    ed = end.replace(tzinfo=None) if end.tzinfo else end

    if st > ed:
        return []

    start_year, start_month = st.year, st.month
    end_year, end_month = ed.year, ed.month

    shards: list[Path] = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        filename = f"{y:04d}-{m:02d}.jsonl"
        p = directory / filename
        if p.exists():
            shards.append(p)
        m += 1
        if m > 12:
            m = 1
            y += 1

    shards.sort()
    return shards


class Store:
    def __init__(self, data_dir: str | Path = "data"):
        self._data = Path(data_dir)
        self._items_dir = self._data / "items"
        self._class_dir = self._data / "classifications"
        self._items_dir.mkdir(parents=True, exist_ok=True)
        self._class_dir.mkdir(parents=True, exist_ok=True)

        self._known_item_ids: set[str] = set()
        self._known_class_keys: set[tuple[str, str, str]] = set()
        self._cls_lock = threading.Lock()
        self._load_indexes()

    def _load_indexes(self) -> None:
        for p in sorted(self._items_dir.glob("*.jsonl")):
            for line in p.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        self._known_item_ids.add(d["id"])
                    except Exception:
                        pass

        for p in sorted(self._class_dir.glob("*.jsonl")):
            for line in p.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        self._known_class_keys.add(
                            (d["item_id"], d["model"], d["config_hash"])
                        )
                    except Exception:
                        pass

    def append_item(self, item: Item) -> bool:
        if item.id in self._known_item_ids:
            return False
        if len(item.raw_text) > _MAX_RAW_CHARS:
            item.raw_text = item.raw_text[:_MAX_RAW_CHARS]
        shard = _shard_key(item.first_seen_at)
        path = self._items_dir / f"{shard}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(item.to_dict()) + "\n")
        self._known_item_ids.add(item.id)
        return True

    def append_classification(self, cls: Classification, force: bool = False) -> bool:
        key = (cls.item_id, cls.model, cls.config_hash)
        with self._cls_lock:
            if key in self._known_class_keys and not force:
                return False
            # force=True: allow a newer classification to supersede an existing one.
            # classifications_as_of() picks the latest by classified_at, so the new
            # record wins without touching earlier records (append-only invariant preserved).
            self._known_class_keys.discard(key)
            shard = _shard_key(cls.classified_at)
            path = self._class_dir / f"{shard}.jsonl"
            with path.open("a") as f:
                f.write(json.dumps(cls.to_dict()) + "\n")
            self._known_class_keys.add(key)
        return True

    def has_classification(self, item_id: str, model: str, config_hash: str) -> bool:
        return (item_id, model, config_hash) in self._known_class_keys

    def items_since(self, since: datetime) -> list[Item]:
        results: list[Item] = []
        now = datetime.now(timezone.utc)
        shards = _get_shards_in_range(self._items_dir, since, now)
        for p in shards:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    item = Item.from_dict(d)
                    if item.first_seen_at >= since:
                        results.append(item)
                except Exception as e:
                    logger.warning("Corrupt item line in %s: %s", p, e)
        return results

    def items_as_of(self, as_of: datetime, days: int) -> list[Item]:
        cutoff = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of
        from datetime import timedelta
        window_start = cutoff - timedelta(days=days)
        results: list[Item] = []
        w_start = window_start.replace(tzinfo=timezone.utc)
        c_end = cutoff.replace(tzinfo=timezone.utc)
        shards = _get_shards_in_range(self._items_dir, w_start, c_end)
        for p in shards:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    item = Item.from_dict(d)
                    fst = item.first_seen_at.replace(tzinfo=None) if item.first_seen_at.tzinfo else item.first_seen_at
                    if window_start <= fst <= cutoff:
                        results.append(item)
                except Exception as e:
                    logger.warning("Corrupt item line in %s: %s", p, e)
        return results

    def classifications_as_of(
        self,
        as_of: datetime,
        model: str,
        config_hash: str,
        since: datetime | None = None,
    ) -> dict[str, Classification]:
        cutoff = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of
        latest: dict[str, Classification] = {}
        if since is not None:
            shards = _get_shards_in_range(self._class_dir, since, as_of)
        else:
            shards = sorted(self._class_dir.glob("*.jsonl"))
        for p in shards:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d["model"] != model or d["config_hash"] != config_hash:
                        continue
                    cls = Classification.from_dict(d)
                    cat = cls.classified_at.replace(tzinfo=None) if cls.classified_at.tzinfo else cls.classified_at
                    if cat <= cutoff:
                        prev = latest.get(cls.item_id)
                        if prev is None or cls.classified_at > prev.classified_at:
                            latest[cls.item_id] = cls
                except Exception as e:
                    logger.warning("Corrupt classification line in %s: %s", p, e)
        return latest

    def all_items_in_range(self, from_dt: datetime, to_dt: datetime) -> list[Item]:
        results: list[Item] = []
        shards = _get_shards_in_range(self._items_dir, from_dt, to_dt)
        for p in shards:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    item = Item.from_dict(d)
                    fst = item.first_seen_at.replace(tzinfo=None) if item.first_seen_at.tzinfo else item.first_seen_at
                    fd = from_dt.replace(tzinfo=None) if from_dt.tzinfo else from_dt
                    td = to_dt.replace(tzinfo=None) if to_dt.tzinfo else to_dt
                    if fd <= fst <= td:
                        results.append(item)
                except Exception as e:
                    logger.warning("Corrupt item line in %s: %s", p, e)
        return results


class StateStore:
    def __init__(self, data_dir: str | Path = "data"):
        self._path = Path(data_dir) / "state.json"
        self._state: dict = {}
        if self._path.exists():
            try:
                self._state = json.loads(self._path.read_text())
            except Exception:
                self._state = {}

    def get(self, key: str, default=None):
        return self._state.get(key, default)

    def set(self, key: str, value) -> None:
        self._state[key] = value

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._state, indent=2, default=str))
