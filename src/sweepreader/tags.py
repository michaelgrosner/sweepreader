"""Controlled tag vocabulary for classification (FUTURE.md Tags feature).

Three independent, multi-select axes. The LLM picks applicable tags from this
closed set; anything outside it is dropped so the UI filter set stays bounded.
"""
from __future__ import annotations

# Ordered so the filter bar groups tags by axis predictably.
TAG_AXES: dict[str, list[str]] = {
    "Subject": [
        "protocol", "order-type", "connectivity", "symbology", "cert-window",
        "new-venue", "rule-filing", "fee-change", "system-status", "margin-capital",
        "surveillance",
    ],
    "Market": ["options", "equities", "futures", "fixed-income"],
    "Action": ["deadline", "action-required", "watch"],
}

ALLOWED_TAGS: set[str] = {t for tags in TAG_AXES.values() for t in tags}
TAG_AXIS: dict[str, str] = {t: axis for axis, tags in TAG_AXES.items() for t in tags}


def sanitize_tags(raw: object) -> list[str]:
    """Normalize and filter an LLM-supplied tag list to the allowed vocabulary."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            continue
        key = t.strip().lower().replace("_", "-").replace(" ", "-")
        if key in ALLOWED_TAGS and key not in out:
            out.append(key)
    return out
