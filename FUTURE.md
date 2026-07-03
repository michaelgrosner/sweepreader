# Future Features

## Tags — ✅ done

Structured, multi-select tag set per item, populated by the LLM and filterable in
the web UI.

**Implemented:**
- Controlled vocabulary in `src/sweepreader/tags.py` (three axes — Subject /
  Market / Action — with `sanitize_tags` to normalize + filter LLM output to the
  closed set so the filter UI stays bounded).
- `Classification.tags: list[str]` (round-trips through the store; back-compat
  default `[]`).
- LLM prompt + JSON schema ask for tags from the axes; `keyword_fallback`
  derives conservative market/`rule-filing` tags without the LLM.
- Tag chips on each card; a faceted filter bar (only tags present in the current
  view, grouped by axis). Client-side filtering combines with the time-travel
  scrubber: **within an axis OR, across axes AND**; empty section headers hide.
- Email digest renders tag chips on the top (A/B) items too (inline styles +
  dark-mode override; the compact "Also worth a look" list stays terse).

Axes: Subject (`protocol` `order-type` `connectivity` `symbology` `cert-window`
`new-venue` `rule-filing` `fee-change` `system-status` `margin-capital`
`surveillance`), Market (`options` `equities` `futures` `fixed-income`), Action
(`deadline` `action-required` `watch`).

Note: existing classifications keep empty tags (config_hash unchanged, so no
forced re-classification). Re-run/backtest under a changed config to populate
tags on historical items.

---

## CBOE PDF spec extraction

CBOE webpage spec enrichment is **✅ done** — `src/sweepreader/ingest/cboe.py`
maps each spec-page RSS URL to its `/revision-history` page and extracts the
latest change-log row into `raw_text` (no JS needed; the table is in the
server-rendered React flight payload). Wired into `RssAdapter.fetch()` for
`cboe_*` sources, with per-item isolation and feed-only fallback.

**Still open — PDF links.** Some feed entries are still direct
`cdn.cboe.com/resources/membership/*.pdf` (e.g. futures specs), which remain
filename-title-only, so the classifier is blind on them.

**Proposed approach** (fallback in the same `cboe.py` module):
- Detect `.pdf` links the existing webpage-enrichment path skips.
- Extract text with the shared `ingest/pdf_text.py` helper (`pdf_to_text`,
  pypdf, already added for BOX) — first ~5 pages, where the revision table sits.
- Parse the front-matter "Revision History" / "Change Log" table; take the
  latest entry's date and description, mirroring the webpage path.
- Use the change-log entry as `raw_text`; fall back to the filename-derived
  title if extraction fails.
- No new dependency needed — `pypdf` and `pdf_to_text` already exist.

