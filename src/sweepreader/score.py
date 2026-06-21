from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store.models import Item, Classification

_HALF_LIFE_DAYS = 7.0


def recency_decay(published_at: datetime, as_of: datetime | None = None) -> float:
    now = as_of or datetime.now(timezone.utc)
    pub = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    n = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (n - pub).total_seconds() / 86400.0)
    return math.exp(-math.log(2) * age_days / _HALF_LIFE_DAYS)


def compute_score(
    item: "Item",
    cls: "Classification",
    config: "AppConfig",
    as_of: datetime | None = None,
) -> float:
    tier_weight = config.tier_weights.get(cls.tier, 0.10)
    decay = recency_decay(item.published_at, as_of)
    return cls.relevance * tier_weight * decay


def is_suppressed(cls: "Classification", config: "AppConfig") -> bool:
    return cls.relevance < config.suppress_threshold or cls.tier == "E"


def rank_items(
    items: list["Item"],
    classifications: dict[str, "Classification"],
    config: "AppConfig",
    as_of: datetime | None = None,
) -> tuple[list[tuple["Item", "Classification", float]], list[tuple["Item", "Classification"]]]:
    """Return (ranked_visible, suppressed). ranked_visible sorted desc by score."""
    visible: list[tuple["Item", "Classification", float]] = []
    suppressed: list[tuple["Item", "Classification"]] = []

    for item in items:
        cls = classifications.get(item.id)
        if cls is None:
            continue
        if is_suppressed(cls, config):
            suppressed.append((item, cls))
        else:
            score = compute_score(item, cls, config, as_of)
            visible.append((item, cls, score))

    visible.sort(key=lambda x: x[2], reverse=True)
    return visible, suppressed
