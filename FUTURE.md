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

## Feedback / voting

Up/down buttons currently write to `data/feedback/YYYY-MM.jsonl` but the data is never read back. Options:
- Feed votes into a per-item score adjustment (decay positive signal, boost negative)
- Use accumulated feedback to fine-tune the profile prompt automatically
- Expose a `backtest --with-feedback` mode that re-ranks using stored signals
