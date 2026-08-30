"""Offline tests for cross-market grouping (GROUPING.md)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sweepreader.grouping import (
    build_groups,
    candidate_groups,
    market_links,
    pick_canonical,
)
from sweepreader.ingest.cluster import slug_stem, venue_group
from sweepreader.store.models import Group, Item
from tests.conftest import FIXTURE_NOW


def make_item(iid, *, venue, title, url, source_id="miax_options", published=None) -> Item:
    pub = published or FIXTURE_NOW
    return Item(
        id=iid, source_id=source_id, venue=venue, title=title, url=url,
        published_at=pub, first_seen_at=pub, raw_text="", modality="scrape",
    )


# --- venue_group -----------------------------------------------------------

def test_venue_group_maps_markets_to_exchange_group():
    assert venue_group("MIAX Sapphire") == "MIAX"
    assert venue_group("MIAX Pearl Equities") == "MIAX"
    assert venue_group("NYSE Bonds") == "NYSE"
    assert venue_group("NYSE Arca Options") == "NYSE"
    assert venue_group("CBOE") == "CBOE"
    assert venue_group("SEC") == "SEC"


def test_venue_group_uses_first_segment_of_slash_joined_venues():
    # NYSE items slash-join several markets into one venue string.
    assert venue_group("NYSE / NYSE American Equities / NYSE Texas") == "NYSE"


# --- slug_stem -------------------------------------------------------------

def test_slug_stem_collapses_miax_market_counter():
    base = "https://www.miaxglobal.com/alert/2026/03/06/x-daylight-saving"
    assert slug_stem(base + "-0") == slug_stem(base + "-4")


def test_slug_stem_collapses_cboe_page_tail():
    base = "https://www.cboe.com/document/tech-spec/content/a/boev3-specification"
    assert slug_stem(base + "/overview") == slug_stem(base)


# --- candidate grouping ----------------------------------------------------

def test_miax_cross_post_groups_across_markets():
    base = "https://www.miaxglobal.com/alert/2026/03/06/daylight-saving"
    items = [
        make_item("a", venue="MIAX Options", title="Daylight Saving", url=base + "-0"),
        make_item("b", venue="MIAX Pearl", title="Daylight Saving", url=base + "-2"),
        make_item("c", venue="MIAX Emerald", title="Daylight Saving", url=base + "-3"),
        make_item("d", venue="MIAX Sapphire", title="Daylight Saving", url=base + "-4"),
    ]
    groups = candidate_groups(items)
    assert len(groups) == 1
    assert len(groups[0]) == 4


def test_grouping_never_spans_exchange_groups():
    """Decision 1: a group stays inside one exchange group."""
    items = [
        make_item("a", venue="MIAX Options", title="Reg SCI Testing",
                  url="https://miaxglobal.com/alert/x"),
        make_item("b", venue="CBOE", title="Reg SCI Testing",
                  url="https://cboe.com/alert/x", source_id="cboe_options_tech"),
    ]
    assert candidate_groups(items) == []


def test_different_days_do_not_group():
    items = [
        make_item("a", venue="NYSE Bonds", title="Redemptions",
                  url="https://nyse.com/a", source_id="nyse_trader_updates"),
        make_item("b", venue="NYSE Bonds", title="Redemptions",
                  url="https://nyse.com/b", source_id="nyse_trader_updates",
                  published=FIXTURE_NOW + timedelta(days=3)),
    ]
    assert candidate_groups(items) == []


def test_similar_titles_do_not_chain_into_one_group():
    """Regression: single-linkage similarity chained 35 unrelated NYSE notices
    into one group. Exact blocking keys must not merge these."""
    url = "https://www.nyse.com/notice/"
    items = [
        make_item("a", venue="NYSE Bonds", title="NYSE Bonds - Redemptions - Traded Bonds",
                  url=url + "a", source_id="nyse_trader_updates"),
        make_item("b", venue="NYSE Bonds", title="NYSE Bonds - Addition to the List of Traded Bonds",
                  url=url + "b", source_id="nyse_trader_updates"),
        make_item("c", venue="NYSE Bonds", title="NYSE Bonds - Removal from the List of Traded Bonds",
                  url=url + "c", source_id="nyse_trader_updates"),
    ]
    assert candidate_groups(items) == []


def test_market_words_are_not_stripped():
    """Options and Equities specs are different documents."""
    items = [
        make_item("a", venue="CBOE", title="Cboe Titanium U.S. Options BOEv3 Specification",
                  url="https://cboe.com/spec-options", source_id="cboe_options_tech"),
        make_item("b", venue="CBOE", title="Cboe Titanium U.S. Equities BOEv3 Specification",
                  url="https://cboe.com/spec-equities", source_id="cboe_options_tech"),
    ]
    assert candidate_groups(items) == []


def test_shared_filing_number_groups_across_sources():
    items = [
        make_item("fr", venue="MEMX", title="Notice SR-MEMX-2026-15",
                  url="https://fr.gov/1", source_id="fed_register_sro"),
        make_item("vn", venue="MEMX", title="MEMX Filing SR-MEMX-2026-15: New Order Type",
                  url="https://memx.com/1", source_id="memx_notices"),
    ]
    groups = candidate_groups(items)
    assert len(groups) == 1 and len(groups[0]) == 2


# --- canonical + ids -------------------------------------------------------

def test_federal_register_is_canonical():
    fr = make_item("fr", venue="MEMX", title="SR-MEMX-2026-15",
                   url="https://fr.gov/1", source_id="fed_register_sro")
    vn = make_item("vn", venue="MEMX", title="SR-MEMX-2026-15",
                   url="https://memx.com/1", source_id="memx_notices")
    assert pick_canonical([vn, fr]).id == "fr"


def test_group_id_is_membership_derived_and_order_independent():
    a = Group.make_id(["x", "y", "z"])
    b = Group.make_id(["z", "y", "x"])
    assert a == b
    assert Group.make_id(["x", "y"]) != a


def test_build_groups_is_deterministic():
    base = "https://www.miaxglobal.com/alert/2026/03/06/daylight"
    items = [
        make_item("a", venue="MIAX Options", title="Daylight", url=base + "-0"),
        make_item("b", venue="MIAX Pearl", title="Daylight", url=base + "-2"),
    ]
    g1 = build_groups(items, now=FIXTURE_NOW)
    g2 = build_groups(list(reversed(items)), now=FIXTURE_NOW)
    assert [g.group_id for g in g1] == [g.group_id for g in g2]
    assert g1[0].canonical_id == g2[0].canonical_id


# --- market links ----------------------------------------------------------

def test_market_links_are_per_member_and_deduped():
    base = "https://www.miaxglobal.com/alert/2026/03/06/daylight"
    items = [
        make_item("a", venue="MIAX Options", title="Daylight", url=base + "-0"),
        make_item("b", venue="MIAX Pearl", title="Daylight", url=base + "-2"),
        make_item("c", venue="MIAX Pearl", title="Daylight", url=base + "-2"),  # dupe
    ]
    links = market_links(items)
    assert links == [("MIAX Options", base + "-0"), ("MIAX Pearl", base + "-2")]


# --- store round-trip ------------------------------------------------------

def test_group_store_round_trip(tmp_path):
    from sweepreader.store.store import Store
    store = Store(tmp_path)
    g = Group(group_id="g1", member_ids=["a", "b"], canonical_id="a",
              decided_at=FIXTURE_NOW, decided_by="llm", confidence=0.9,
              summary="one event", model="m")
    assert store.append_group(g) is True
    assert store.append_group(g) is False        # membership-keyed: no churn
    assert store.has_group("g1")

    got = store.groups_as_of(FIXTURE_NOW + timedelta(days=1))
    assert got["g1"].summary == "one event"
    assert got["g1"].decided_by == "llm"

    reloaded = Store(tmp_path)
    assert reloaded.has_group("g1")


def test_group_dict_round_trip():
    g = Group(group_id="g", member_ids=["b", "a"], canonical_id="a",
              decided_at=datetime(2026, 6, 21, tzinfo=timezone.utc))
    assert Group.from_dict(g.to_dict()) == g


# --- LLM adjudication (offline) --------------------------------------------

def _adj_group(members):
    ids = sorted(m.id for m in members)
    return Group(group_id=Group.make_id(ids), member_ids=ids,
                 canonical_id=ids[0], decided_at=FIXTURE_NOW)


def _cboe_pair():
    base = "https://www.cboe.com/document/tech-spec/content/a/boev3-specification"
    return [
        make_item("a", venue="CBOE", title="Cboe Titanium BOEv3 Specification",
                  url=base, source_id="cboe_options_tech"),
        make_item("b", venue="CBOE", title="Cboe Titanium BOEv3 Specification",
                  url=base + "/overview", source_id="cboe_equities_tech"),
    ]


def test_prompt_covers_multi_url_case_not_just_per_market():
    """The Cboe pattern is same-venue, differing feed/URL — the prompt must
    surface both signals or the model cannot decide it."""
    from sweepreader.classify.grouper import _build_prompt
    members = _cboe_pair()
    prompt = _build_prompt([(_adj_group(members), members)])
    assert "cboe_options_tech" in prompt and "cboe_equities_tech" in prompt
    assert "/overview" in prompt
    assert "several URLs or feeds" in prompt


def test_adjudicate_drops_rejected_groups():
    from unittest.mock import patch

    from sweepreader.classify import grouper
    members = _cboe_pair()
    group = _adj_group(members)
    with patch.object(grouper, "_post", return_value={"groups": [
            {"index": 0, "same_event": False, "confidence": 0.9}]}):
        out = grouper.adjudicate([group], {m.id: m for m in members},
                                 _cfg(), api_key="k", now=FIXTURE_NOW)
    assert out == []


def test_adjudicate_keeps_confirmed_group_with_summary():
    from unittest.mock import patch

    from sweepreader.classify import grouper
    members = _cboe_pair()
    group = _adj_group(members)
    with patch.object(grouper, "_post", return_value={"groups": [
            {"index": 0, "same_event": True, "confidence": 0.8, "summary": "One spec."}]}):
        out = grouper.adjudicate([group], {m.id: m for m in members},
                                 _cfg(), api_key="k", now=FIXTURE_NOW)
    assert len(out) == 1
    assert out[0].summary == "One spec." and out[0].decided_by == "llm"
    assert out[0].group_id == group.group_id      # membership key unchanged


def test_adjudicate_keeps_heuristic_group_when_model_fails():
    """A failed call must not silently discard grouping."""
    from unittest.mock import patch

    from sweepreader.classify import grouper
    members = _cboe_pair()
    group = _adj_group(members)
    with patch.object(grouper, "_post", return_value=None):
        out = grouper.adjudicate([group], {m.id: m for m in members},
                                 _cfg(), api_key="k", now=FIXTURE_NOW)
    assert len(out) == 1 and out[0].decided_by == "heuristic"


def _cfg():
    from sweepreader.config import AppConfig
    return AppConfig(model="m", suppress_threshold=35, trailing_days=14,
                     profile_prompt="p",
                     tier_weights={"A": 1.0, "B": 0.85, "C": 0.55, "D": 0.4, "E": 0.1},
                     sources=[], max_age_days=183)


# --- parallel SRO filings by sibling entities -------------------------------

def _fr(iid, entity, subject, venue, day=None):
    return make_item(iid, venue=venue, source_id="fed_register_sro",
                     title=f"Self-Regulatory Organizations; {entity}; {subject}",
                     url=f"https://federalregister.gov/{iid}", published=day)


def test_sibling_miax_entities_group():
    """MIAX files the same rule change separately per exchange; the entity name
    also appears inside the subject."""
    subj_a = ("Notice of Filing and Immediate Effectiveness of a Proposed Rule Change To "
              "Delay Implementation of a Change to Rule 515A, MIAX Emerald Price Improvement "
              'Mechanism ("PRIME") and PRIME Solicitation Mechanism')
    subj_b = ("Notice of Filing and Immediate Effectiveness of a Proposed Rule Change To "
              "Delay Implementation of a Change to Rule 515A, MIAX Price Improvement "
              'Mechanism ("PRIME") and PRIME Solicitation Mechanism')
    items = [_fr("a", "MIAX Emerald, LLC", subj_a, "MIAX"),
             _fr("b", "Miami International Securities Exchange, LLC", subj_b, "MIAX")]
    groups = candidate_groups(items)
    assert len(groups) == 1 and len(groups[0]) == 2


def test_sibling_nasdaq_entities_group():
    subj = ("Notice of Filing and Immediate Effectiveness of Proposed Rule Change To Amend "
            "the Exchange's Connectivity Schedule and Discontinue a Previously Proposed Offering")
    items = [_fr("a", "Nasdaq MRX, LLC", subj, "NASDAQ"),
             _fr("b", "Nasdaq ISE, LLC", subj, "NASDAQ")]
    assert len(candidate_groups(items)) == 1


def test_sro_grouping_still_respects_exchange_group():
    subj = "Notice of Filing To Amend the Fee Schedule"
    items = [_fr("a", "MIAX Emerald, LLC", subj, "MIAX"),
             _fr("b", "Cboe BZX Exchange, Inc.", subj, "CBOE")]
    assert candidate_groups(items) == []


def test_sro_grouping_still_respects_the_day():
    subj = "Notice of Filing To Amend the Fee Schedule"
    items = [_fr("a", "MIAX Emerald, LLC", subj, "MIAX"),
             _fr("b", "MIAX PEARL, LLC", subj, "MIAX", day=FIXTURE_NOW + timedelta(days=2))]
    assert candidate_groups(items) == []


def test_different_sro_subjects_do_not_group():
    items = [_fr("a", "MIAX Emerald, LLC", "Notice of Filing To Amend the Fee Schedule", "MIAX"),
             _fr("b", "MIAX PEARL, LLC", "Notice of Filing To Adopt a New Order Type", "MIAX")]
    assert candidate_groups(items) == []


# --- stale group records ----------------------------------------------------

def test_superseded_group_does_not_claim_members(tmp_path):
    """group_id encodes membership, so a grown group leaves the old record behind
    and both claim the shared members. Newest must win."""
    from datetime import datetime as _dt

    from sweepreader.render.page import _collapse
    from sweepreader.store.models import Classification

    base = "https://www.miaxglobal.com/alert/2026/06/21/daylight"
    a = make_item("a", venue="MIAX Options", title="Daylight", url=base + "-0")
    b = make_item("b", venue="MIAX Pearl", title="Daylight", url=base + "-2")
    c = make_item("c", venue="MIAX Emerald", title="Daylight", url=base + "-3")
    items = [a, b, c]

    def cls(iid):
        return Classification(item_id=iid, model="m", config_hash="h",
                              classified_at=FIXTURE_NOW, relevance=80, tier="A",
                              rationale="r", summary="s")
    classifications = {i.id: cls(i.id) for i in items}

    old = Group(group_id=Group.make_id(["a", "b"]), member_ids=["a", "b"],
                canonical_id="a", decided_at=_dt(2026, 6, 20, tzinfo=timezone.utc))
    new = Group(group_id=Group.make_id(["a", "b", "c"]), member_ids=["a", "b", "c"],
                canonical_id="a", decided_at=_dt(2026, 6, 21, tzinfo=timezone.utc))

    visible = [(i, classifications[i.id], 10.0) for i in items]
    cards, _ = _collapse(visible, [], {old.group_id: old, new.group_id: new},
                         items, classifications)
    assert len(cards) == 1
    assert cards[0].member_count == 3
