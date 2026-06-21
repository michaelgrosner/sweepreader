from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sweepreader.store.models import Item

_FILING_RE = re.compile(r'SR-([A-Z]+-\d{4}-\d+)', re.I)
_CLOSE_WINDOW = timedelta(hours=72)


def _filing_number(item: "Item") -> str | None:
    if item.cluster_id:
        m = _FILING_RE.search(item.cluster_id)
        if m:
            return m.group(1).upper()
    m = _FILING_RE.search(item.title)
    if m:
        return m.group(1).upper()
    return None


def _title_tokens(title: str) -> set[str]:
    title = re.sub(r'[^a-z0-9 ]', ' ', title.lower())
    words = title.split()
    stopwords = {'the', 'a', 'an', 'of', 'and', 'or', 'to', 'in', 'for', 'on', 'at',
                 'by', 'with', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'shall',
                 'should', 'may', 'might', 'must', 'can', 'could', 'from', 'that',
                 'this', 'its', 'it', 'inc', 'llc', 'corp', 'exchange', 'self', 'regulatory'}
    return {w for w in words if w not in stopwords and len(w) > 2}


def _title_similarity(a: str, b: str) -> float:
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def assign_clusters(items: list["Item"]) -> list["Item"]:
    """
    Assign cluster_ids to items that describe the same underlying event.
    Canonical source preference: federal_register for rule filings; venue's own feed for operational.
    Mutates items in place, returns them.
    """
    # Group by filing number first (highest confidence)
    by_filing: dict[str, list["Item"]] = {}
    for item in items:
        fn = _filing_number(item)
        if fn:
            by_filing.setdefault(fn, []).append(item)

    for fn, group in by_filing.items():
        if len(group) < 2:
            continue
        canonical_id = _pick_canonical(group, fn)
        for item in group:
            item.cluster_id = canonical_id

    # Second pass: title similarity + close timestamps for remaining unclustered
    unclustered = [i for i in items if not _has_filing(i)]
    for i, item_a in enumerate(unclustered):
        for item_b in unclustered[i+1:]:
            if item_a.cluster_id and item_a.cluster_id == item_b.cluster_id:
                continue
            if item_a.venue != item_b.venue:
                continue
            dt_a = item_a.published_at
            dt_b = item_b.published_at
            if abs((dt_a - dt_b).total_seconds()) > _CLOSE_WINDOW.total_seconds():
                continue
            sim = _title_similarity(item_a.title, item_b.title)
            if sim >= 0.6:
                canonical = _pick_canonical([item_a, item_b], None)
                item_a.cluster_id = canonical
                item_b.cluster_id = canonical

    return items


def _has_filing(item: "Item") -> bool:
    return bool(item.cluster_id and _FILING_RE.search(item.cluster_id or ""))


def _pick_canonical(group: list["Item"], filing_number: str | None) -> str:
    if filing_number:
        # Prefer Federal Register for rule filings
        fr = [i for i in group if i.source_id.startswith("fed_register")]
        if fr:
            return fr[0].id
    # Prefer the source with the highest-priority source_id (lower = earlier in config)
    return group[0].id
