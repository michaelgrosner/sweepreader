from datetime import datetime, timezone

from sweepreader.ingest.cluster import assign_clusters
from sweepreader.store.models import Item


def make_item(iid, source_id, title, cluster_id=None) -> Item:
    now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    return Item(
        id=iid,
        source_id=source_id,
        venue="MEMX",
        title=title,
        url=f"https://example.com/{iid}",
        published_at=now,
        first_seen_at=now,
        raw_text="",
        modality="rss",
        cluster_id=cluster_id,
    )


def test_same_filing_clusters_together():
    item_fr = make_item("fr_1", "fed_register_sro",
                        "Self-Regulatory Organizations; MEMX LLC; Notice SR-MEMX-2026-15",
                        cluster_id="SR-MEMX-2026-15")
    item_venue = make_item("memx_1", "memx_notices",
                           "MEMX Filing SR-MEMX-2026-15: New Order Type")

    result = assign_clusters([item_fr, item_venue])
    assert item_fr.cluster_id == item_venue.cluster_id, "Same filing should be in same cluster"


def test_federal_register_is_canonical():
    item_fr = make_item("fr_1", "fed_register_sro",
                        "SR-MEMX-2026-15 Notice",
                        cluster_id="SR-MEMX-2026-15")
    item_venue = make_item("memx_1", "memx_notices",
                           "MEMX SR-MEMX-2026-15 Filing")

    assign_clusters([item_fr, item_venue])
    # canonical id should be the fed_register item's id
    assert item_fr.cluster_id == item_fr.id
    assert item_venue.cluster_id == item_fr.id


def test_different_filings_not_clustered():
    item_a = make_item("a1", "memx_notices", "SR-MEMX-2026-15 notice", cluster_id="SR-MEMX-2026-15")
    item_b = make_item("b1", "memx_notices", "SR-MEMX-2026-16 notice", cluster_id="SR-MEMX-2026-16")

    assign_clusters([item_a, item_b])
    assert item_a.cluster_id != item_b.cluster_id


def test_unrelated_items_not_clustered():
    item_a = make_item("a1", "cboe_options_tech", "C2 Spec Update: New Message Type")
    item_b = make_item("b1", "cboe_options_tech", "BZX Connectivity Test Window Scheduled")

    assign_clusters([item_a, item_b])
    # These are unrelated (low title similarity) so should not share a cluster_id
    assert item_a.cluster_id != item_b.cluster_id or (item_a.cluster_id is None and item_b.cluster_id is None)
