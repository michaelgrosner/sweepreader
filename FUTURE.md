# Future Features

## Tags

Apply a structured tag set to each item alongside tier/relevance. The LLM would populate tags as part of the classification response; tags would be filterable on the page.

**Proposed axes** (independent, multi-select):

| Axis | Tags |
|---|---|
| **Subject** | `protocol` `order-type` `connectivity` `symbology` `cert-window` `new-venue` `rule-filing` `fee-change` `system-status` `margin-capital` `surveillance` |
| **Market** | `options` `equities` `futures` `fixed-income` |
| **Action** | `deadline` `action-required` `watch` |

**Implementation sketch:**
- Add `tags: list[str]` to `Classification` dataclass
- Add tag extraction to the LLM prompt and JSON schema
- Render tag chips on each card
- Client-side filter bar on the page (similar to the existing scrubber)

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

---

## Feedback / voting

Up/down buttons currently write to `data/feedback/YYYY-MM.jsonl` but the data is never read back. Options:
- Feed votes into a per-item score adjustment (decay positive signal, boost negative)
- Use accumulated feedback to fine-tune the profile prompt automatically
- Expose a `backtest --with-feedback` mode that re-ranks using stored signals
