"""Tests for deadline extraction, validation, store query, page rail, and email rail.

Follows DEADLINES.md requirements:
- schema round-trip: to_dict/from_dict with both fields set and both absent
- absent fields on a stored record load as None (back-compat)
- config_hash() is unchanged by adding the fields — assert explicitly
- validation: bad ISO string, out-of-range year, unknown kind, kind-without-date
- rail selection: an item outside trailing_days but with a near deadline is included;
  one with a deadline 90 days out is not
- urgency buckets at the 3 / 14 / 45 day boundaries, and the past-date grace day
- clock pinned to FIXTURE_NOW (2026-06-21)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import sweepreader.render.page as page_mod
from sweepreader.classify.classifier import _build_prompt, _validate_response
from sweepreader.config import AppConfig
from sweepreader.deadlines import (
    DEADLINE_KINDS,
    build_deadline_row,
    sanitize_deadline_kind,
    select_deadlines,
)
from sweepreader.render.email_render import render_email
from sweepreader.render.page import render_page
from sweepreader.store import StateStore, Store
from sweepreader.store.models import Classification, Group, Item
from tests.conftest import FIXTURE_NOW


def make_config() -> AppConfig:
    return AppConfig(
        model="anthropic/claude-haiku-4-5",
        suppress_threshold=35,
        trailing_days=14,
        profile_prompt="Director-level engineer, US options market making",
        tier_weights={"A": 1.0, "B": 0.85, "C": 0.55, "D": 0.40, "E": 0.10},
        sources=[],
        max_age_days=183,
        page_url="https://example.com/sweepreader/",
    )


def make_item(
    item_id: str = "item123",
    published_at: datetime | None = None,
    title: str = "CBOE Tech Notice",
    venue: str = "CBOE",
) -> Item:
    pub = published_at or FIXTURE_NOW
    return Item(
        id=item_id,
        source_id="cboe_tech",
        venue=venue,
        title=title,
        url=f"https://example.com/notices/{item_id}",
        published_at=pub,
        first_seen_at=pub,
        raw_text="Notice content with cutover date 2026-06-28.",
        modality="rss",
    )


def test_deadline_vocabulary_and_sanitization():
    expected_kinds = {
        "cert-window-opens",
        "cert-window-closes",
        "cutover",
        "upgrade-by",
        "comment-closes",
        "retirement",
    }
    assert DEADLINE_KINDS == expected_kinds
    assert sanitize_deadline_kind("cutover") == "cutover"
    assert sanitize_deadline_kind("CERT_WINDOW_OPENS") == "cert-window-opens"
    assert sanitize_deadline_kind("  comment closes  ") == "comment-closes"
    assert sanitize_deadline_kind("upgrade-by") == "upgrade-by"
    assert sanitize_deadline_kind("unknown-kind") is None
    assert sanitize_deadline_kind(None) is None
    assert sanitize_deadline_kind(123) is None


def test_schema_round_trip():
    # Both fields set
    now = FIXTURE_NOW
    cls = Classification(
        item_id="item1",
        model="test-model",
        config_hash="abc",
        classified_at=now,
        relevance=85,
        tier="A",
        rationale="Spec change",
        summary="Cboe adds new binary protocol format.",
        venues=["CBOE"],
        tags=["protocol", "cert-window"],
        deadline_date=date(2026, 7, 15),
        deadline_kind="cert-window-closes",
    )
    d = cls.to_dict()
    assert d["deadline_date"] == "2026-07-15"
    assert d["deadline_kind"] == "cert-window-closes"

    loaded = Classification.from_dict(d)
    assert loaded.deadline_date == date(2026, 7, 15)
    assert loaded.deadline_kind == "cert-window-closes"
    assert loaded.to_dict() == d

    # Both fields absent / None
    cls_empty = Classification(
        item_id="item2",
        model="test-model",
        config_hash="abc",
        classified_at=now,
        relevance=40,
        tier="C",
        rationale="Fee filing",
        summary=None,
        deadline_date=None,
        deadline_kind=None,
    )
    d_empty = cls_empty.to_dict()
    assert d_empty["deadline_date"] is None
    assert d_empty["deadline_kind"] is None

    loaded_empty = Classification.from_dict(d_empty)
    assert loaded_empty.deadline_date is None
    assert loaded_empty.deadline_kind is None


def test_back_compat_absent_fields_on_stored_record():
    """Historical records stored prior to deadline extraction have no deadline fields."""
    legacy_dict = {
        "item_id": "legacy1",
        "model": "claude-haiku-4-5",
        "config_hash": "a1b2c3d4e5f6",
        "classified_at": FIXTURE_NOW.isoformat(),
        "relevance": 70,
        "tier": "B",
        "rationale": "Market structure note",
        "summary": "SEC proposal on order display.",
        "venues": ["SEC"],
        "tags": ["rule-filing"],
        "unclassified": False,
    }
    cls = Classification.from_dict(legacy_dict)
    assert cls.deadline_date is None
    assert cls.deadline_kind is None


def test_config_hash_is_unchanged_by_deadlines():
    """Crucial requirement: deadline fields must NOT be part of config_hash().

    Adding them would invalidate all 4,900+ stored classifications and cost a full re-run.
    """
    cfg = make_config()
    orig_hash = cfg.config_hash()

    # Classification object with or without deadlines has zero impact on config_hash
    cls_with_deadline = Classification(
        item_id="i", model=cfg.model, config_hash=orig_hash,
        classified_at=FIXTURE_NOW, relevance=90, tier="A", rationale="r", summary="s",
        deadline_date=date(2026, 7, 1), deadline_kind="cutover",
    )
    assert cls_with_deadline.config_hash == orig_hash
    assert cfg.config_hash() == orig_hash


def test_validation_rules():
    item = make_item(published_at=datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))

    # Base valid classification dict
    base = {
        "relevance": 80,
        "tier": "A",
        "venues": ["CBOE"],
        "rationale": "Valid notice",
        "summary": "Valid summary",
    }

    # 1. Bad ISO string -> treated as None rather than failing the whole response
    data_bad_iso = dict(base, deadline_date="not-an-iso-date", deadline_kind="cutover")
    assert _validate_response(data_bad_iso, item) is True
    assert data_bad_iso["deadline_date"] is None
    assert data_bad_iso["deadline_kind"] == "cutover"

    # 2. Out-of-range year: more than 2 years in past (historical rule echo)
    data_past_2y = dict(base, deadline_date="2023-01-01", deadline_kind="upgrade-by")
    assert _validate_response(data_past_2y, item) is True
    assert data_past_2y["deadline_date"] is None

    # 3. Out-of-range year: more than 5 years in future (typo)
    data_future_5y = dict(base, deadline_date="2035-06-20", deadline_kind="cutover")
    assert _validate_response(data_future_5y, item) is True
    assert data_future_5y["deadline_date"] is None

    # 4. Valid date within [-2y, +5y] bounds
    data_valid_date = dict(base, deadline_date="2026-07-15", deadline_kind="cutover")
    assert _validate_response(data_valid_date, item) is True
    assert data_valid_date["deadline_date"] == date(2026, 7, 15)
    assert data_valid_date["deadline_kind"] == "cutover"

    # 5. Unknown kind -> treated as None
    data_bad_kind = dict(base, deadline_date="2026-07-15", deadline_kind="invalid_kind")
    assert _validate_response(data_bad_kind, item) is True
    assert data_bad_kind["deadline_date"] == date(2026, 7, 15)
    assert data_bad_kind["deadline_kind"] is None

    # 6. Kind without date -> kind preserved, date None
    data_kind_no_date = dict(base, deadline_date=None, deadline_kind="cutover")
    assert _validate_response(data_kind_no_date, item) is True
    assert data_kind_no_date["deadline_date"] is None
    assert data_kind_no_date["deadline_kind"] == "cutover"

    # 7. Date without kind -> date preserved, kind None
    data_date_no_kind = dict(base, deadline_date="2026-08-01", deadline_kind=None)
    assert _validate_response(data_date_no_kind, item) is True
    assert data_date_no_kind["deadline_date"] == date(2026, 8, 1)
    assert data_date_no_kind["deadline_kind"] is None


def test_prompt_includes_do_not_infer_clause():
    item = make_item()
    cfg = make_config()
    prompt = _build_prompt(item, cfg, cfg.suppress_threshold)
    assert "deadline_date" in prompt
    assert "deadline_kind" in prompt
    assert "Do not infer, extrapolate, or convert a relative phrase" in prompt
    assert "The item's own publication date is never a deadline." in prompt
    # A date the reader only observes (a listing going live, a halt) is not a
    # deadline, and having one must not lift the item out of tier E.
    assert "A date the reader merely observes is not a deadline." in prompt
    assert "The presence of a date does not raise an item's tier or relevance." in prompt


def test_urgency_buckets_and_grace_day():
    today = FIXTURE_NOW.date()  # 2026-06-21

    # Grace window: today - 1d -> days_remaining = -1, is_past = True, included
    item_grace = make_item("i_grace")
    cls_grace = Classification("i_grace", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                               deadline_date=today - timedelta(days=1))
    row_grace = build_deadline_row(item_grace, cls_grace, today)
    assert row_grace is not None
    assert row_grace.days_remaining == -1
    assert row_grace.is_past is True
    assert row_grace.urgency == "urgent"
    assert row_grace.remaining_str == "-1d"

    # Beyond grace window: today - 2d -> excluded from rail
    item_old = make_item("i_old")
    cls_old = Classification("i_old", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                             deadline_date=today - timedelta(days=2))
    rows = select_deadlines([(item_old, cls_old)], today, max_days=45)
    assert len(rows) == 0

    # Today boundary (0d) -> urgent
    cls_0 = Classification("i_0", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                           deadline_date=today)
    row_0 = build_deadline_row(make_item("i_0"), cls_0, today)
    assert row_0 is not None
    assert row_0.days_remaining == 0
    assert row_0.urgency == "urgent"

    # 3d boundary -> urgent (<= 3 red)
    cls_3 = Classification("i_3", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                           deadline_date=today + timedelta(days=3))
    row_3 = build_deadline_row(make_item("i_3"), cls_3, today)
    assert row_3 is not None
    assert row_3.days_remaining == 3
    assert row_3.urgency == "urgent"

    # 4d boundary -> warning (<= 14 amber)
    cls_4 = Classification("i_4", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                           deadline_date=today + timedelta(days=4))
    row_4 = build_deadline_row(make_item("i_4"), cls_4, today)
    assert row_4 is not None
    assert row_4.days_remaining == 4
    assert row_4.urgency == "warning"

    # 14d boundary -> warning (<= 14 amber)
    cls_14 = Classification("i_14", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                            deadline_date=today + timedelta(days=14))
    row_14 = build_deadline_row(make_item("i_14"), cls_14, today)
    assert row_14 is not None
    assert row_14.days_remaining == 14
    assert row_14.urgency == "warning"

    # 15d boundary -> neutral
    cls_15 = Classification("i_15", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                            deadline_date=today + timedelta(days=15))
    row_15 = build_deadline_row(make_item("i_15"), cls_15, today)
    assert row_15 is not None
    assert row_15.days_remaining == 15
    assert row_15.urgency == "neutral"

    # 45d boundary -> neutral, included
    cls_45 = Classification("i_45", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                            deadline_date=today + timedelta(days=45))
    row_45 = build_deadline_row(make_item("i_45"), cls_45, today)
    assert row_45 is not None
    assert row_45.days_remaining == 45
    assert row_45.urgency == "neutral"
    rows_45 = select_deadlines([(make_item("i_45"), cls_45)], today, max_days=45)
    assert len(rows_45) == 1

    # 46d -> excluded from 45d rail
    cls_46 = Classification("i_46", "m", "h", FIXTURE_NOW, 80, "A", "r", "s",
                            deadline_date=today + timedelta(days=46))
    rows_46 = select_deadlines([(make_item("i_46"), cls_46)], today, max_days=45)
    assert len(rows_46) == 0


def test_rail_selection_and_lookback_beyond_trailing_days(tmp_path, monkeypatch):
    """Items older than trailing_days (14d) but with near deadlines must appear in rail."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(page_mod, "_DOCS_DIR", docs_dir)

    store = Store(tmp_path / "data")
    state = StateStore(tmp_path / "data")
    config = make_config()
    today = FIXTURE_NOW.date()  # 2026-06-21

    # Item 1: published 25 days ago (outside 14d trailing window, but within max_age_days 183d)
    # Has a near deadline closing in 7 days (2026-06-28)
    pub_old = FIXTURE_NOW - timedelta(days=25)
    item_old = make_item("old_near_deadline", published_at=pub_old, title="Old Notice Near Deadline")
    cls_old = Classification(
        item_id=item_old.id, model=config.model, config_hash=config.config_hash(),
        classified_at=pub_old, relevance=80, tier="A", rationale="r", summary="Summary",
        deadline_date=today + timedelta(days=7), deadline_kind="cert-window-closes",
    )
    store.append_item(item_old)
    store.append_classification(cls_old)

    # Item 2: published 2 days ago, but deadline is 90 days in future (outside 45d rail lookahead)
    pub_recent = FIXTURE_NOW - timedelta(days=2)
    item_far = make_item("recent_far_deadline", published_at=pub_recent, title="Recent Notice Far Deadline")
    cls_far = Classification(
        item_id=item_far.id, model=config.model, config_hash=config.config_hash(),
        classified_at=pub_recent, relevance=80, tier="A", rationale="r", summary="Summary",
        deadline_date=today + timedelta(days=90), deadline_kind="cutover",
    )
    store.append_item(item_far)
    store.append_classification(cls_far)

    render_page(config, store, state, now=FIXTURE_NOW)

    html = (docs_dir / "index.html").read_text()

    # Assert near deadline from 25-day-old notice is included in deadline rail
    assert "Upcoming deadlines" in html
    assert "Old Notice Near Deadline" in html
    assert "cert-window-closes" in html
    assert "#item-old_near_deadline" in html
    assert "7d" in html

    # Assert 90-day deadline item is NOT in the rail
    assert "Recent Notice Far Deadline" not in html.split('id="deadline-rail"')[1].split('</section>')[0]


def test_page_renders_card_chip_and_scrubber_filter(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(page_mod, "_DOCS_DIR", docs_dir)

    store = Store(tmp_path / "data")
    state = StateStore(tmp_path / "data")
    config = make_config()
    today = FIXTURE_NOW.date()

    item = make_item("cutover_item", published_at=FIXTURE_NOW, title="Major Exchange Cutover")
    cls = Classification(
        item_id=item.id, model=config.model, config_hash=config.config_hash(),
        classified_at=FIXTURE_NOW, relevance=95, tier="A", rationale="Migration", summary="Cutover details",
        deadline_date=today + timedelta(days=2), deadline_kind="cutover",
    )
    store.append_item(item)
    store.append_classification(cls)

    render_page(config, store, state, now=FIXTURE_NOW)
    html = (docs_dir / "index.html").read_text()

    # Scrubber button
    assert 'id="scrubber-deadline"' in html
    # Card deadline chip
    assert "deadline-chip" in html
    assert "⏱ cutover · 2026-06-23 · 2d" in html
    assert 'data-has-deadline="true"' in html



def test_email_digest_rail_restricted_to_14_days(tmp_path):
    store = Store(tmp_path / "data")
    state = StateStore(tmp_path / "data")
    config = make_config()
    today = FIXTURE_NOW.date()

    # Item A: deadline in 5 days (<= 14d) -> should be in email rail
    item_a = make_item("item_near", published_at=FIXTURE_NOW, title="Item Near Deadline")
    cls_a = Classification(
        item_id=item_a.id, model=config.model, config_hash=config.config_hash(),
        classified_at=FIXTURE_NOW, relevance=85, tier="A", rationale="r", summary="s",
        deadline_date=today + timedelta(days=5), deadline_kind="cutover",
    )
    store.append_item(item_a)
    store.append_classification(cls_a)

    # Item B: deadline in 30 days (> 14d) -> must NOT be in email rail
    item_b = make_item("item_30d", published_at=FIXTURE_NOW, title="Item 30 Days Out")
    cls_b = Classification(
        item_id=item_b.id, model=config.model, config_hash=config.config_hash(),
        classified_at=FIXTURE_NOW, relevance=85, tier="A", rationale="r", summary="s",
        deadline_date=today + timedelta(days=30), deadline_kind="cert-window-opens",
    )
    store.append_item(item_b)
    store.append_classification(cls_b)

    html = render_email(config, store, state, dry_run=True, now=FIXTURE_NOW)

    assert "Upcoming deadlines · next 14 days" in html
    assert "Item Near Deadline" in html
    assert "5d" in html

    # Item 30 Days Out must NOT appear in the deadline block
    deadline_block = html.split("Upcoming deadlines · next 14 days")[1].split("New since last digest")[0]
    assert "Item 30 Days Out" not in deadline_block


def test_rail_collapses_a_cross_posted_group_into_one_row(tmp_path, monkeypatch):
    """One notice published to six markets is one card, so it is one rail row.

    Before this, the rail was built per item: the group took six lines and five
    of their `#item-<id>` anchors pointed at ids the page never rendered, since
    a grouped card only exists under its display member's id.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(page_mod, "_DOCS_DIR", docs_dir)

    store = Store(tmp_path / "data")
    state = StateStore(tmp_path / "data")
    config = make_config()
    today = FIXTURE_NOW.date()

    venues = ["MIAX Pearl", "MIAX Emerald", "MIAX Options", "MIAX Sapphire"]
    member_ids = []
    for n, venue in enumerate(venues):
        item = make_item(
            f"miax_{n}",
            published_at=FIXTURE_NOW,
            title="MIAX Exchange Group - 45-day retention period for SFTP reports",
            venue=venue,
        )
        store.append_item(item)
        store.append_classification(Classification(
            item_id=item.id, model=config.model, config_hash=config.config_hash(),
            classified_at=FIXTURE_NOW, relevance=85, tier="A", rationale="r",
            summary="Retention period change.",
            deadline_date=today + timedelta(days=26), deadline_kind="cutover",
        ))
        member_ids.append(item.id)

    store.append_group(Group(
        group_id=Group.make_id(member_ids), member_ids=member_ids,
        canonical_id=member_ids[0], decided_at=FIXTURE_NOW,
    ))

    render_page(config, store, state, now=FIXTURE_NOW)
    html = (docs_dir / "index.html").read_text()
    rail = html.split('id="deadline-rail"')[1].split("</section>")[0]

    assert rail.count('class="deadline-row') == 1
    assert "4 notices" in rail
    # The row points at the card the page actually rendered.
    assert f'href="#item-{member_ids[0]}"' in rail
    for stale in member_ids[1:]:
        assert f"#item-{stale}" not in rail


def test_suppressed_item_with_a_date_stays_out_of_the_rail(tmp_path, monkeypatch):
    """A tier E notice does not earn rail space by carrying a date.

    Nasdaq listing notices ("... to Begin Listing and Trading on 09/16/2026")
    are tier E, but the model occasionally reads the trading date as a cutover.
    The rail is built from visible cards, so suppressed noise cannot surface.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(page_mod, "_DOCS_DIR", docs_dir)

    store = Store(tmp_path / "data")
    state = StateStore(tmp_path / "data")
    config = make_config()
    today = FIXTURE_NOW.date()

    noise = make_item("listing_noise", published_at=FIXTURE_NOW,
                      title="DTN2026-19 - Trio-Tech to Begin Listing and Trading on Nasdaq",
                      venue="NASDAQ")
    store.append_item(noise)
    store.append_classification(Classification(
        item_id=noise.id, model=config.model, config_hash=config.config_hash(),
        classified_at=FIXTURE_NOW, relevance=10, tier="E", rationale="Corporate action",
        summary=None, deadline_date=today + timedelta(days=6), deadline_kind="cutover",
    ))

    real = make_item("real_cutover", published_at=FIXTURE_NOW, title="Real Cutover Notice")
    store.append_item(real)
    store.append_classification(Classification(
        item_id=real.id, model=config.model, config_hash=config.config_hash(),
        classified_at=FIXTURE_NOW, relevance=85, tier="A", rationale="Migration",
        summary="s", deadline_date=today + timedelta(days=6), deadline_kind="cutover",
    ))

    render_page(config, store, state, now=FIXTURE_NOW)
    html = (docs_dir / "index.html").read_text()
    rail = html.split('id="deadline-rail"')[1].split("</section>")[0]

    assert "Real Cutover Notice" in rail
    assert "Trio-Tech" not in rail
    # Still on the page, in the suppressed list.
    assert "Trio-Tech" in html
