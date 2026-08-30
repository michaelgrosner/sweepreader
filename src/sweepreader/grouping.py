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


def _group_keys(item: "Item") -> list[tuple[str, ...]]:
    """All exact keys this item can group on. Items sharing ANY key group together.

    An item needs more than one key because the same fan-out shows up differently
    per member: MIAX serves the base alert at `.../interface` and its siblings at
    `.../interface-0`, `-2`, `-3`. Keying each item a single way put the base URL
    on a title key and its siblings on a slug key, so they never matched despite
    having the same stem AND the same title.

    Every key is exact — no fuzzy similarity. That is what keeps the transitive
    union safe; an earlier similarity-based version chained 35 unrelated NYSE
    notices into one group.
    """
    keys: list[tuple[str, ...]] = []
    vg = venue_group(item.venue)
    day = item.published_at.date().isoformat()

    filing = _filing_number(item)
    if filing:
        keys.append(("filing", filing))

    stem = slug_stem(item.url)
    if stem:
        keys.append(("slug", vg, stem))

    if item.source_id.startswith("fed_register"):
        # Parallel filings by sibling entities are one regulatory action. Candidates
        # only — the model sees the full titles and can still reject.
        subject = _sro_subject(item.title, vg)
        if subject:
            keys.append(("sro", vg, subject, day))

    title = _norm_title(item.title)
    if title:
        keys.append(("title", vg, title, day))
    return keys


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def candidate_groups(items: list["Item"]) -> list[list["Item"]]:
    """Groups of size >= 2 connected by at least one shared exact key."""
    by_id = {i.id: i for i in items}
    holders: dict[tuple[str, ...], list[str]] = {}
    for item in items:
        for key in _group_keys(item):
            holders.setdefault(key, []).append(item.id)

    uf = _UnionFind()
    for ids in holders.values():
        for other in ids[1:]:
            uf.union(ids[0], other)

    buckets: dict[str, list[str]] = {}
    for iid in by_id:
        buckets.setdefault(uf.find(iid), []).append(iid)

    groups = []
    for member_ids in buckets.values():
        if len(member_ids) < 2:
            continue
        groups.append(sorted((by_id[m] for m in member_ids),
                             key=lambda x: (x.published_at, x.id)))
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
