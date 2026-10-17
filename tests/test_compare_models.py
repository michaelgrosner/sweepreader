"""Tests for the model-comparison harness — pure pieces only, no network or CLI."""
from __future__ import annotations

import random
from datetime import datetime, timezone

from sweepreader.cli.compare_models import (
    _fr_raw_text_url,
    aggregate,
    blind,
    select_sample,
)
from sweepreader.store.models import Classification, Item

_T = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _item(iid: str, source: str, text: str = "x" * 200) -> Item:
    return Item(id=iid, source_id=source, venue="CBOE", title=f"t{iid}", url=f"https://e/{iid}",
                published_at=_T, first_seen_at=_T, raw_text=text, modality="api")


def _cls(iid: str, tier: str) -> Classification:
    return Classification(item_id=iid, model="m", config_hash="h", classified_at=_T,
                          relevance=50, tier=tier, rationale="r", summary=None, venues=[])


def test_fr_raw_text_url_from_html_url():
    url = "https://www.federalregister.gov/documents/2026/10/19/2026-21247/self-regulatory-organizations"
    assert _fr_raw_text_url(url) == \
        "https://www.federalregister.gov/documents/full_text/text/2026/10/19/2026-21247.txt"
    assert _fr_raw_text_url("https://example.com/x") is None


def test_sample_spreads_sources_and_favours_substantive():
    items = [_item(f"a{i}", "miax") for i in range(20)] + [_item(f"b{i}", "iex") for i in range(20)]
    stored = {it.id: _cls(it.id, "B" if i % 2 else "E") for i, it in enumerate(items)}
    picked = select_sample(items, stored, 10, random.Random(0))
    assert len(picked) == 10 and len({p.id for p in picked}) == 10
    assert {p.source_id for p in picked} == {"miax", "iex"}
    assert sum(stored[p.id].tier != "E" for p in picked) == 7


def test_sample_skips_short_text_except_federal_register():
    items = [_item("short", "miax", "tiny"), _item("fr", "fed_register_sro", "title only")]
    assert {p.id for p in select_sample(items, {}, 5, random.Random(0))} == {"fr"}


def test_blind_is_reproducible_and_varies_by_pass():
    outputs = {m: {"m": m} for m in ("m1", "m2", "m3", "m4", "m5")}
    first = blind("item", outputs, 0)
    assert first == blind("item", outputs, 0)
    assert [lab for lab, _, _ in first] == ["A", "B", "C", "D", "E"]
    orders = {tuple(m for _, m, _ in blind("item", outputs, p)) for p in range(6)}
    assert len(orders) > 1


def test_aggregate_penalises_invalid_and_counts_wins():
    gens = {
        ("good", "i1"): {"valid": True, "cost": 0.001, "latency_s": 1.0, "source_id": "s"},
        ("ok", "i1"): {"valid": True, "cost": 0.002, "latency_s": 2.0, "source_id": "s"},
        ("broken", "i1"): {"valid": False, "cost": 0.0, "latency_s": 3.0, "source_id": "s"},
    }
    judgments = [{
        "item_id": "i1", "pass": 0, "labels": {"A": "ok", "B": "good"},
        "verdict": {
            "candidates": [
                {"label": "A", "accuracy": 6, "substance": 5, "classification": 7, "overall": 6, "note": ""},
                {"label": "B", "accuracy": 9, "substance": 8, "classification": 8, "overall": 9, "note": ""},
            ],
            "ranking": ["B", "A"],
        },
    }]
    rep = aggregate(gens, judgments)
    assert rep["good"]["overall"] == 9 and rep["good"]["win_pct"] == 100 and rep["good"]["mean_rank"] == 1
    assert rep["ok"]["mean_rank"] == 2
    assert rep["broken"]["overall"] == 1 and rep["broken"]["mean_rank"] == 3
    assert rep["broken"]["valid_pct"] == 0
    assert rep["good"]["usd_per_1k"] == 1.0


def test_eval_body_applies_overrides():
    from sweepreader.classify.classifier import request_body
    from sweepreader.cli.compare_models import eval_body
    assert eval_body("deepseek/deepseek-v4.1-flash", "p") == request_body("deepseek/deepseek-v4.1-flash", "p", max_tokens=2048)
    assert "temperature" not in eval_body("anthropic/claude-haiku-5.5", "p")
    glm = eval_body("z-ai/glm-5.3-flash", "p")
    assert glm["reasoning"] == {"effort": "low"} and glm["max_tokens"] == 8192
