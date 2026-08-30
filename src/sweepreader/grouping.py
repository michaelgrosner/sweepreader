"""Group items that describe one underlying event.

Exchange groups publish the same notice once per market, and some serve one
document under several URLs, so item-level dedup cannot catch it — the URLs
genuinely differ. See GROUPING.md.

Candidate generation here is deliberately cheap and runs over the whole render
window rather than one run's new items, which is what makes cross-run duplicates
group at all. The LLM only adjudicates the candidates this produces.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sweepreader.ingest.cluster import (
    _filing_number,
    slug_stem,
    venue_group,
)
from sweepreader.store.models import Group

if TYPE_CHECKING:
    from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

def _norm_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Deliberately does NOT
    strip market words — "Options BOEv3" and "Equities BOEv3" are different
    documents and must not collapse together."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", title.lower()).split())


# Sibling legal entities within one exchange group. An exchange group files the
# same rule change separately for each of its exchanges, so the Federal Register
# carries near-identical notices differing only by entity name — both in the
# "Self-Regulatory Organizations; <ENTITY>;" prefix and inside the subject
# ("MIAX Emerald Price Improvement Mechanism" vs "MIAX Price Improvement
# Mechanism"). Neutralizing these tokens lets the parallel filings share a key.
# Only ever applied within a single exchange group on a single day.
_SRO_ENTITY_TOKENS: dict[str, frozenset[str]] = {
    "MIAX": frozenset({"miax", "miami", "international", "securities", "emerald",
                       "pearl", "sapphire"}),
    "NASDAQ": frozenset({"nasdaq", "ise", "mrx", "gemx", "phlx", "bx", "omx",
                         "stock", "market"}),
    "NYSE": frozenset({"nyse", "arca", "american", "national", "texas", "chicago",
                       "bonds"}),
    "CBOE": frozenset({"cboe", "bzx", "byx", "edgx", "edga", "bze", "c1", "c2"}),
}
_SRO_GENERIC_TOKENS = frozenset({"llc", "inc", "exchange", "exchanges", "the", "s"})

_SRO_PREFIX = re.compile(r"^self\s+regulatory\s+organizations\s+", re.I)


def _sro_subject(title: str, group: str) -> str:
    """Federal Register title reduced to its subject, with the filing entity's
    identity removed so sibling filings collapse together."""
    norm = _norm_title(title)
    norm = _SRO_PREFIX.sub("", norm)
    drop = _SRO_ENTITY_TOKENS.get(group, frozenset()) | _SRO_GENERIC_TOKENS
    return " ".join(w for w in norm.split() if w not in drop)


def _group_key(item: "Item") -> tuple[str, ...] | None:
    """Exact blocking key, or None if the item should never be grouped.

    Deliberately exact rather than fuzzy. An earlier attempt used pairwise title
    similarity with union-find, which chained 35 unrelated NYSE notices into one
    group through transitivity — single-linkage clustering on formulaic titles
    ("NYSE Bonds - Redemptions..." vs "NYSE Bonds - Addition...") merges almost
    everything. Exact keys cannot chain.
    """
    filing = _filing_number(item)
    if filing:
        return ("filing", filing)

    vg = venue_group(item.venue)
    day = item.published_at.date().isoformat()

    stem = slug_stem(item.url)
    if stem and stem != urlparse(item.url).path.rstrip("/"):
        # The URL carried a market counter or a page tail, so the stem is
        # evidence of fan-out rather than just the path.
        return ("slug", vg, stem)

    if item.source_id.startswith("fed_register"):
        # Parallel filings by sibling entities are one regulatory action. These are
        # candidates only — the model still has to confirm, and it sees the full
        # titles with entity names intact so it can reject a false pairing.
        subject = _sro_subject(item.title, vg)
        if subject:
            return ("sro", vg, subject, day)

    title = _norm_title(item.title)
    if not title:
        return None
    return ("title", vg, title, day)


def candidate_groups(items: list["Item"]) -> list[list["Item"]]:
    """Groups of size >= 2 sharing an exact blocking key."""
    buckets: dict[tuple[str, ...], list["Item"]] = {}
    for item in items:
        key = _group_key(item)
        if key is None:
            continue
        buckets.setdefault(key, []).append(item)

    groups = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        groups.append(sorted(members, key=lambda x: (x.published_at, x.id)))
    return groups


def pick_canonical(members: list["Item"]) -> "Item":
    """Federal Register wins for rule filings; otherwise the earliest published,
    tie-broken by id so the choice is stable across runs."""
    fr = [m for m in members if m.source_id.startswith("fed_register")]
    pool = fr or members
    return min(pool, key=lambda m: (m.published_at, m.id))


def build_groups(items: list["Item"], *, now: datetime | None = None) -> list[Group]:
    """Heuristic groups over `items`. Deterministic: the same input yields the
    same group_ids, so re-running does not churn the store."""
    now = now or datetime.now(timezone.utc)
    out: list[Group] = []
    for members in candidate_groups(items):
        member_ids = sorted(m.id for m in members)
        out.append(Group(
            group_id=Group.make_id(member_ids),
            member_ids=member_ids,
            canonical_id=pick_canonical(members).id,
            decided_at=now,
            decided_by="heuristic",
            confidence=1.0,
        ))
    return out


def market_links(members: list["Item"]) -> list[tuple[str, str]]:
    """(market label, url) per member, for the chips on a grouped card.

    Decision 4: each entry links to its own notice, which is what makes
    collapsing lossless. Deduped on (label, url) because a group can legitimately
    contain two notices from the same market.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for m in sorted(members, key=lambda x: (x.venue, x.id)):
        key = (m.venue, m.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
