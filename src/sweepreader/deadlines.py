"""Deadline extraction and formatting utilities (DEADLINES.md).

Scope is dates the reader must act before:
- certification / testing windows (open and close)
- production cutover or migration dates
- mandatory upgrade-by dates for a protocol or spec version
- comment-period closes on filings that affect market-making obligations
- retirement of a feed, port, protocol version or symbology scheme
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sweepreader.store.models import Classification, Item

DEADLINE_KINDS: set[str] = {
    "cert-window-opens",
    "cert-window-closes",
    "cutover",
    "upgrade-by",
    "comment-closes",
    "retirement",
}


def sanitize_deadline_kind(raw: object) -> str | None:
    """Normalize and filter an LLM-supplied deadline kind to the closed set."""
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower().replace("_", "-").replace(" ", "-")
    return key if key in DEADLINE_KINDS else None


@dataclass
class DeadlineRow:
    """A formatted deadline entry for the page rail or email digest."""
    item: "Item"
    cls: "Classification"
    deadline_date: date
    deadline_kind: str | None
    days_remaining: int
    urgency: str       # "urgent" (<= 3d), "warning" (<= 14d), "neutral" (> 14d)
    is_past: bool      # days_remaining < 0 (1-day grace window)
    remaining_str: str # e.g. "3d", "0d", "-1d"


def build_deadline_row(
    item: "Item",
    cls: "Classification",
    today: date,
) -> DeadlineRow | None:
    """Build a DeadlineRow for an item/classification pair if a date is set."""
    if cls.deadline_date is None:
        return None
    days = (cls.deadline_date - today).days
    is_past = days < 0
    if days <= 3:
        urgency = "urgent"
    elif days <= 14:
        urgency = "warning"
    else:
        urgency = "neutral"
    return DeadlineRow(
        item=item,
        cls=cls,
        deadline_date=cls.deadline_date,
        deadline_kind=cls.deadline_kind,
        days_remaining=days,
        urgency=urgency,
        is_past=is_past,
        remaining_str=f"{days}d",
    )


def select_deadlines(
    items_and_cls: list[tuple["Item", "Classification"]],
    today: date,
    max_days: int = 45,
) -> list[DeadlineRow]:
    """Select and sort deadline rows within [today - 1d, today + max_days].

    Past dates within the 1-day grace window (today - 1d) are included and marked
    as past; older expired dates drop off. Sorted ascending by deadline_date.
    """
    min_date = today - timedelta(days=1)
    max_date = today + timedelta(days=max_days)
    rows: list[DeadlineRow] = []

    for item, cls in items_and_cls:
        if cls.deadline_date is not None and min_date <= cls.deadline_date <= max_date:
            row = build_deadline_row(item, cls, today)
            if row is not None:
                rows.append(row)

    rows.sort(key=lambda r: (r.deadline_date, r.item.id))
    return rows
