from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sweepreader.grouping import market_links
from sweepreader.score import rank_items
from sweepreader.tags import TAG_AXES

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store import StateStore, Store
    from sweepreader.store.models import Classification, Group, Item


@dataclass
class Card:
    """One rendered card. `members` is empty for an ungrouped item; for a group it
    holds every member, and the card shows the canonical one (GROUPING.md §3.4)."""
    item: "Item"
    cls: "Classification"
    score: float
    members: list["Item"] = field(default_factory=list)
    markets: list[tuple[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str | None = None
    first_seen_at: datetime | None = None

    @property
    def is_group(self) -> bool:
        return len(self.members) > 1

    @property
    def member_count(self) -> int:
        return len(self.members) or 1

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"
_DOCS_DIR = Path("docs")

_TIER_COLORS = {
    "A": "#4F46E5",
    "B": "#3B82F6",
    "C": "#14B8A6",
    "D": "#F59E0B",
    "E": "#9CA3AF",
}


def _is_today(dt: datetime, now: datetime) -> bool:
    n = now.replace(tzinfo=None) if now.tzinfo else now
    d = dt.replace(tzinfo=None) if dt.tzinfo else dt
    today_start = n.replace(hour=0, minute=0, second=0, microsecond=0)
    return d >= today_start


def _consistent_members(
    group: "Group",
    member_to_group: dict[str, "Group"],
    by_id: dict[str, "Item"],
) -> list["Item"]:
    """Members that are present and still resolve to this group."""
    return [
        by_id[m] for m in group.member_ids
        if m in by_id and member_to_group.get(m) is group
    ]


def _collapse(
    visible: list[tuple["Item", "Classification", float]],
    suppressed: list[tuple["Item", "Classification"]],
    groups: dict[str, "Group"],
    items: list["Item"],
    classifications: dict[str, "Classification"],
) -> tuple[list["Card"], list["Card"]]:
    """Fold grouped items into one card each, preserving rank order.

    A group is represented by its canonical member when that member is visible,
    otherwise by its highest-scoring visible member — so a group is never dropped
    just because the canonical item scored below the suppression threshold.
    Suppressed members of a surfaced group are absorbed into its card rather than
    listed separately.
    """
    by_id = {i.id: i for i in items}

    # The group store is append-only and group_id encodes membership, so a group
    # whose membership later changed leaves its old record behind. Both records
    # claim the shared members. Resolve newest-wins, then keep only members that
    # agree on the same group so a card can never show a stale membership.
    member_to_group: dict[str, "Group"] = {}
    for g in sorted(groups.values(), key=lambda x: x.decided_at):
        for mid in g.member_ids:
            member_to_group[mid] = g

    cards: list[Card] = []
    seen_groups: set[str] = set()

    for item, cls, score in visible:          # already sorted desc by score
        group = member_to_group.get(item.id)
        if group is None:
            cards.append(Card(item=item, cls=cls, score=score,
                              tags=list(cls.tags), summary=cls.summary,
                              first_seen_at=item.first_seen_at))
            continue
        if group.group_id in seen_groups:
            continue                          # absorbed into the card already emitted
        seen_groups.add(group.group_id)

        members = _consistent_members(group, member_to_group, by_id)
        if len(members) < 2:
            cards.append(Card(item=item, cls=cls, score=score,
                              tags=list(cls.tags), summary=cls.summary,
                              first_seen_at=item.first_seen_at))
            continue

        # Prefer the canonical member as the face of the card, but only if it is
        # itself visible; the current (highest-scoring) item is the fallback.
        display_item, display_cls = item, cls
        if group.canonical_id != item.id:
            for vi, vc, _vs in visible:
                if vi.id == group.canonical_id:
                    display_item, display_cls = vi, vc
                    break

        tags: list[str] = []
        for m in members:
            mc = classifications.get(m.id)
            if mc:
                for tag in mc.tags:
                    if tag not in tags:
                        tags.append(tag)

        cards.append(Card(
            item=display_item,
            cls=display_cls,
            score=score,
            members=members,
            markets=market_links(members),
            tags=tags,
            summary=group.summary or display_cls.summary,
            first_seen_at=min(m.first_seen_at for m in members),
        ))

    # Suppressed members of a surfaced group are already represented by its card.
    absorbed = {m for c in cards for m in (x.id for x in c.members)}
    remaining = [(i, c) for i, c in suppressed if i.id not in absorbed]

    # Fully-suppressed groups still collapse. Most cross-posting sits below the
    # relevance threshold, so leaving these expanded would keep the bulk of the
    # duplication on the page.
    sup_cards: list[Card] = []
    seen_sup: set[str] = set()
    for item, cls in remaining:
        group = member_to_group.get(item.id)
        if group is None:
            sup_cards.append(Card(item=item, cls=cls, score=0.0,
                                  tags=list(cls.tags), summary=cls.summary,
                                  first_seen_at=item.first_seen_at))
            continue
        if group.group_id in seen_sup:
            continue
        seen_sup.add(group.group_id)
        members = _consistent_members(group, member_to_group, by_id)
        if len(members) < 2:
            sup_cards.append(Card(item=item, cls=cls, score=0.0,
                                  tags=list(cls.tags), summary=cls.summary,
                                  first_seen_at=item.first_seen_at))
            continue
        sup_tags: list[str] = []
        for m in members:
            mc = classifications.get(m.id)
            if mc:
                for tag in mc.tags:
                    if tag not in sup_tags:
                        sup_tags.append(tag)
        sup_cards.append(Card(
            item=item, cls=cls, score=0.0, members=members,
            markets=market_links(members), tags=sup_tags,
            summary=group.summary or cls.summary,
            first_seen_at=min(m.first_seen_at for m in members),
        ))
    return cards, sup_cards


def render_page(
    config: "AppConfig",
    store: "Store",
    state: "StateStore",
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.trailing_days)

    items = store.items_as_of(now, config.trailing_days)
    classifications = store.classifications_as_of(now, config_hash=config.config_hash(), since=cutoff)

    from sweepreader.tags import ALLOWED_TAGS
    for cls in classifications.values():
        cls.tags = [t for t in cls.tags if t in ALLOWED_TAGS]

    visible, suppressed = rank_items(items, classifications, config, now)

    groups = store.groups_as_of(now, since=cutoff) if config.grouping_enabled else {}
    cards, suppressed_cards = _collapse(visible, suppressed, groups, items, classifications)

    new_today = [c for c in cards if _is_today(c.item.published_at, now)]
    earlier = [c for c in cards if not _is_today(c.item.published_at, now)]

    # Tags actually present across rendered items, grouped by axis (so the filter
    # bar only offers tags that exist in the current view).
    present_set: set[str] = set()
    for card in cards:
        present_set.update(card.tags)
    for card in suppressed_cards:
        present_set.update(card.tags)
    filter_axes = {
        axis: [t for t in tags if t in present_set]
        for axis, tags in TAG_AXES.items()
    }
    filter_axes = {axis: tags for axis, tags in filter_axes.items() if tags}

    source_health = state.get("source_health", {})
    failures = state.get("failures_this_run", 0)

    enabled_sources = [s for s in config.sources if s.is_active(now)]
    coverage_codes = sorted({s.id.replace("_tech", "").replace("_reg", "").upper()[:8] for s in enabled_sources})

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["tier_color"] = lambda t: _TIER_COLORS.get(t, "#9CA3AF")
    env.filters["score_pct"] = lambda s: min(100, int(s))
    env.filters["is_today"] = lambda dt: _is_today(dt, now)

    template = env.get_template("page.html")
    html = template.render(
        now=now,
        new_today=new_today,
        earlier=earlier,
        suppressed=suppressed_cards,
        coverage_codes=coverage_codes,
        source_health=source_health,
        failures=failures,
        model=config.model,
        trailing_days=config.trailing_days,
        tier_colors=_TIER_COLORS,
        tier_weights=config.tier_weights,
        filter_axes=filter_axes,
    )

    _DOCS_DIR.mkdir(exist_ok=True)
    (_DOCS_DIR / "index.html").write_text(html)
    logger.info("Page rendered: %d card(s) from %d visible item(s), %d suppressed row(s)",
                len(cards), len(visible), len(suppressed_cards))
