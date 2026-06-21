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

## CBOE RSS PDF extraction

CBOE technical RSS feeds link almost entirely to PDFs (spec sheets, BOE protocol docs, etc.) rather than web pages. The current classifier only sees the filename as the title and no body text, making classification nearly blind.

**Proposed approach:**
- In `RssAdapter.fetch()`, detect PDF links (`.pdf` extension)
- Fetch and extract text with `pypdf` or `pdfminer.six` (first ~5 pages only — release notes are always at the front)
- Look for a "Revision History" or "Change Log" table; extract the latest entry date and description
- Use the extracted date as `published_at` and the change-log entry as `raw_text` for classification
- Fall back to filename-derived title if extraction fails

This would also fix the date problem (CBOE PDFs have no `<pubDate>` in the RSS, only a file modification timestamp).

---

## Feedback / voting

Up/down buttons currently write to `data/feedback/YYYY-MM.jsonl` but the data is never read back. Options:
- Feed votes into a per-item score adjustment (decay positive signal, boost negative)
- Use accumulated feedback to fine-tune the profile prompt automatically
- Expose a `backtest --with-feedback` mode that re-ranks using stored signals
