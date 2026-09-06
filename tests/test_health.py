"""Source-health page rows.

The clock is pinned (see conftest.FIXTURE_NOW) because `stale_days` is measured
against it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sweepreader.config import AppConfig, SourceConfig
from sweepreader.render.page import build_health_rows
from sweepreader.store import StateStore

from .conftest import FIXTURE_NOW


def _state(health: dict) -> StateStore:
    """A StateStore over a directory that has no state.json, so it starts empty."""
    state = StateStore(data_dir="/nonexistent-for-tests")
    state.set("source_health", health)
    return state


def _src(sid: str, *, enabled: bool = True, disabled_until: date | None = None) -> SourceConfig:
    return SourceConfig(id=sid, modality="rss", parse="rss_generic",
                        default_tier_hint="B", weight=0.9, enabled=enabled,
                        endpoint=f"https://example.test/{sid}.xml",
                        disabled_until=disabled_until)


def _config(*sources: SourceConfig) -> AppConfig:
    return AppConfig(model="m", suppress_threshold=35, trailing_days=14,
                     profile_prompt="p", tier_weights={"A": 1.0}, sources=list(sources),
                     max_age_days=183)


def _rows(config, health):
    return {r.source_id: r for r in build_health_rows(config, _state(health), FIXTURE_NOW)}


def test_ok_source_reports_item_count_and_last_ok():
    ok_at = (FIXTURE_NOW - timedelta(days=2)).isoformat()
    rows = _rows(_config(_src("a")), {"a": {"status": "ok", "item_count": 7, "last_ok": ok_at}})
    row = rows["a"]
    assert row.status == "ok"
    assert row.item_count == 7
    assert row.stale_days == 2


def test_failing_source_keeps_the_earlier_success_and_reports_staleness():
    """The whole point of last_ok: a 10-day outage must not look like a new one."""
    ok_at = (FIXTURE_NOW - timedelta(days=10)).isoformat()
    rows = _rows(_config(_src("a")), {"a": {"status": "error", "error": "503", "last_ok": ok_at}})
    row = rows["a"]
    assert row.status == "error"
    assert row.detail == "503"
    assert row.stale_days == 10
    assert row.item_count is None


def test_config_disabled_source_is_off_not_unknown():
    rows = _rows(_config(_src("a", enabled=False)), {})
    assert rows["a"].status == "off"
    assert "config.yaml" in rows["a"].detail


def test_paused_source_shows_its_resume_date():
    cfg = _config(_src("a", disabled_until=date(2026, 9, 13)))
    rows = _rows(cfg, {"a": {"status": "disabled"}})
    assert rows["a"].status == "disabled"
    assert "2026-09-13" in rows["a"].detail


def test_never_fetched_source_is_unknown():
    assert _rows(_config(_src("a")), {})["a"].status == "unknown"


def test_rows_are_ordered_worst_first():
    cfg = _config(_src("z_ok"), _src("y_err"), _src("x_warn"), _src("w_off", enabled=False))
    health = {"z_ok": {"status": "ok"}, "y_err": {"status": "error"},
              "x_warn": {"status": "warning"}}
    order = [r.status for r in build_health_rows(cfg, _state(health), FIXTURE_NOW)]
    assert order == ["error", "warning", "ok", "off"]


def test_naive_timestamps_are_treated_as_utc():
    """state.json is written with `default=str`, which can drop the offset."""
    naive = datetime(2026, 6, 19, 12, 0, 0).isoformat()
    rows = _rows(_config(_src("a")), {"a": {"status": "error", "last_ok": naive}})
    assert rows["a"].last_ok == datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
    assert rows["a"].stale_days == 2


def test_unparseable_timestamp_degrades_to_none():
    rows = _rows(_config(_src("a")), {"a": {"status": "ok", "last_ok": "not-a-date"}})
    assert rows["a"].last_ok is None
    assert rows["a"].stale_days is None
