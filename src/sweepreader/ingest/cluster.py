from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from sweepreader.store.models import Item

_FILING_RE = re.compile(r'SR-([A-Z]+-\d{4}-\d+)', re.I)
_CLOSE_WINDOW = timedelta(hours=72)

# `venue` is the individual market ("MIAX Sapphire", "NYSE Bonds"), not the
# exchange group. Grouping is scoped to one exchange group (GROUPING.md
# decision 1), so pairs are compared on the group, not the raw venue.
_VENUE_GROUPS = (
    "MIAX", "NYSE", "CBOE", "NASDAQ", "IEX", "MEMX", "BOX",
    "OCC", "OPRA", "SEC", "FINRA", "CAT", "ICE",
)


def venue_group(venue: str) -> str:
    """Map a per-market venue to its exchange group.

    "MIAX Sapphire" -> "MIAX";  "NYSE Arca Options" -> "NYSE";  "SEC" -> "SEC".
    Some NYSE items slash-join several markets into one string; the first
    segment decides the group.
    """
    v = venue.strip().upper()
    for g in _VENUE_GROUPS:
        if v == g or v.startswith(g + " ") or v.startswith(g + "/"):
            return g
    if "/" in v:
        return venue_group(v.split("/", 1)[0])
    return v


_TRAILING_COUNTER = re.compile(r'-\d+$')
_PAGE_TAILS = ("/overview", "/introduction", "/summary")


def slug_stem(url: str) -> str:
    """Normalize a URL to the document it points at.

    MIAX publishes one alert per market as `...-daylight-saving-0/-2/-3/-4`, and
    Cboe serves one spec at both `.../specification` and `.../specification/overview`.
    Both collapse to the same stem.
    """
    path = urlparse(url).path.rstrip("/")
    for tail in _PAGE_TAILS:
        if path.endswith(tail):
            path = path[: -len(tail)]
            break
    return _TRAILING_COUNTER.sub("", path)


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
            if venue_group(item_a.venue) != venue_group(item_b.venue):
                continue
            dt_a = item_a.published_at
            dt_b = item_b.published_at
            if abs((dt_a - dt_b).total_seconds()) > _CLOSE_WINDOW.total_seconds():
                continue
            same_doc = slug_stem(item_a.url) == slug_stem(item_b.url)
            sim = _title_similarity(item_a.title, item_b.title)
            if sim >= 0.6 or same_doc:
                if item_a.cluster_id and item_b.cluster_id:
                    # Merge clusters: set all items in the list with item_b's cluster_id to item_a's cluster_id
                    old_id = item_b.cluster_id
                    new_id = item_a.cluster_id
                    for item in items:
                        if item.cluster_id == old_id:
                            item.cluster_id = new_id
                elif item_a.cluster_id:
                    item_b.cluster_id = item_a.cluster_id
                elif item_b.cluster_id:
                    item_a.cluster_id = item_b.cluster_id
                else:
                    canonical = _pick_canonical([item_a, item_b], None)
                    item_a.cluster_id = canonical
                    item_b.cluster_id = canonical

    return items


def _has_filing(item: "Item") -> bool:
    return _filing_number(item) is not None


def _pick_canonical(group: list["Item"], filing_number: str | None) -> str:
    if filing_number:
        # Prefer Federal Register for rule filings
        fr = [i for i in group if i.source_id.startswith("fed_register")]
        if fr:
            return fr[0].id
    # Prefer the source with the highest-priority source_id (lower = earlier in config)
    return group[0].id
