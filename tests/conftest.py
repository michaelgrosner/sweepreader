"""Shared test fixtures.

The ingest adapters and the page renderer filter on a lookback window measured
from "now". Tests therefore pin the clock rather than reading it, so a suite
that passes today still passes next year — see FIXTURE_NOW below.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Anchored just after the newest entry in tests/fixtures/*. The captured
# fixtures are real upstream samples and are deliberately left untouched;
# pinning the clock beside them keeps the assertions deterministic.
FIXTURE_NOW = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
