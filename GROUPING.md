# Proposal: LLM-assisted grouping of duplicate notices

Status: **proposal, not implemented.** Measurements below are from the committed
corpus as of 2026-08-30 (4,822 items in `data/items/`).

## 1. The problem, measured

Exchange groups publish one notice per market, and some publish the same document
under several URLs. Item-level dedup cannot catch this: the URLs genuinely differ,
so `Item.make_id(source_id, url)` correctly produces distinct items. The duplication
is *semantic*.

On the current 14-day render window (270 cards), collapsing only **exact** title
matches removes 101 cards — a **37% reduction** — across 42 groups.

Across the full corpus, 2,965 items (61%) share a normalized title with at least
one other item, and 75 groups span more than one `source_id`.

### Real examples

MIAX posts one alert per market as four separate pages:

```
miax_options   .../2026/03/06/...-daylight-saving-0
miax_options   .../2026/03/06/...-daylight-saving-2
miax_options   .../2026/03/06/...-daylight-saving-3
miax_options   .../2026/03/06/...-daylight-saving-4
miax_equities  (same title again)
```

Cboe serves one spec under two URL paths, and all three Cboe tech feeds carry it:

```
cboe_options_tech    .../cboe-titanium-u.s.-options-boev3-specification
cboe_options_tech    .../cboe-titanium-u.s.-options-boev3-specification/overview
cboe_equities_tech   (both variants)
cboe_futures_tech    (both variants)
```

Top offenders in the current window: `NYSE Bonds - Addition to the List of Traded
Bonds` (5x), `MIAX Exchange Group - Options & Equities Markets - Pre-Test` (5x),
`NYSE Bonds - Redemptions` (4x).

## 2. What already exists, and why it under-delivers

`src/sweepreader/ingest/cluster.py` already implements filing-number matching plus a
title-similarity fallback, and `run.py:111` calls it. `SPEC.md:131` already specifies
the intended output — *"Render one card, all source links."* Three things stop that
from happening.

**(a) Clusters are computed but never rendered.** There is not a single reference to
`cluster` anywhere in `src/sweepreader/render/` or `templates/`. The `cluster_id` is
assigned, persisted, and then ignored at display time. This is the single largest gap
and it requires no LLM at all.

**(b) Clustering only ever sees one run's new items.** `assign_clusters(all_new_items)`
receives only items new in that run. Anything whose twin arrived in an earlier run is
never compared. This is visible in the data — the same Cboe spec carries three
different `cluster_id`s from three consecutive days:

```
2026-06-29  cluster=6a5a83001f33b5397b22de4e
2026-06-30  cluster=3385bc194af0b5ec5efc118c
2026-07-01  cluster=0d2b9a558218679bff2251ed
```

With a 3-hour cadence and a 14-day window, most duplicates arrive in different runs.

**(c) The same-venue guard blocks the exact case we care about.** `cluster.py:69`
skips any pair where `item_a.venue != item_b.venue`. Critically, `venue` is *not* an
exchange group — it is the individual market. The four MIAX copies above carry four
different venues and, provably, no cluster:

```
venue=MIAX Sapphire   cluster=None
venue=MIAX Pearl      cluster=None
venue=MIAX Emerald    cluster=None
venue=MIAX Options    cluster=None
```

So the guard is the direct cause of the largest duplicate class in the corpus. The
venue vocabulary is per-market throughout — `MIAX Options/Sapphire/Pearl/Emerald/
Futures/Pearl Equities`, `NYSE Bonds`, `NYSE Arca Options`, and so on.

Net effect: 34% of items carry a `cluster_id`, but only 381 items (7.9%) are in a
cluster with more than one member, and 1,273 cluster_ids have exactly one member.

## 3. Proposed design

### 3.1 Do the free part first

Before any LLM work, render the clusters that already exist and fix (b) by clustering
against the stored window rather than just the run's new items. Expect most of the
37% exact-title win from this alone. This is Phase 0 below and is worth landing on its
own regardless of what follows.

### 3.2 Where the LLM belongs

Not on every pair — that is O(n²) and the window holds ~270 items (~36k pairs). The LLM
should adjudicate only *candidate* pairs that cheap heuristics have already shortlisted.

```
  ingest ─▶ candidate generation (free) ─▶ LLM adjudication (cheap) ─▶ group store ─▶ UI
             normalized title, shared      "same underlying event?"    persistent
             filing no., date proximity,   yes/no + canonical pick     group_id
             URL slug stem
```

**Candidate generation** (no model calls) should shortlist a pair when any of:
- normalized titles match exactly, or token-Jaccard ≥ 0.6 (the existing function)
- both carry the same `SR-XXX-YYYY-NN` filing number (already implemented)
- URL slugs match after stripping a trailing `-\d+` or `/overview` style suffix —
  this alone catches both the MIAX and Cboe cases above
- published within 72h of each other

**Do not drop the venue guard — narrow it.** Per decision (1) below, grouping stays
inside one exchange group. Since `venue` is per-market, the guard must compare
*exchange groups*, not venues, which needs a mapping that does not exist yet:

```python
# MIAX Options/Sapphire/Pearl/Emerald/Futures/Pearl Equities -> MIAX
# NYSE / NYSE Arca * / NYSE American * / NYSE Bonds / NYSE Texas -> NYSE
# Cboe BZX/BYX/EDGX/EDGA/C1/C2 -> CBOE
_VENUE_GROUP: dict[str, str]
```

This is the single highest-value change in the whole proposal: it is a few lines, needs
no model, and directly unblocks the MIAX and Cboe fan-outs.

**LLM adjudication** takes a batch of candidate groups and returns, per group, whether
the items describe one underlying event, which item is canonical, and — per decision
(2) — a purpose-written group summary rather than reusing the canonical item's. Batch
many groups per call; this is a cheap classification, not the main relevance judgment.

The group summary lives in the group record, never in `Classification`. Key its
freshness on the membership set (`sha256` of sorted `member_ids`) so it is written once
per distinct group and only rewritten when a member is added or removed — otherwise a
5-member group would be re-summarized on every one of the eight daily runs.

Rough cost: ~42 candidate groups per window, batched ~10 per call, ≈5 extra calls per
run against the existing ~270 classification calls. Under 2% overhead.

### 3.3 Metadata and storage — the important constraint

**Do not put group membership in `Classification`, and do not add it to
`config_hash()`.** Two reasons, both load-bearing:

1. `Classification` is keyed `(item_id, config_hash)` and is a strictly per-item
   judgment. Grouping is a property *between* items; a group changes when a new member
   arrives, which would force rewriting an unrelated item's classification record.
2. Anything added to `config_hash()` invalidates all 4,900+ stored classifications and
   forces a full re-run against the LLM. See `CLAUDE.md`.

Instead add a third store alongside items and classifications:

```
data/groups/YYYY-MM.jsonl
  {"group_id": "...", "member_ids": [...], "canonical_id": "...",
   "confidence": 0.0-1.0, "decided_by": "llm"|"heuristic",
   "summary": "...", "decided_at": "...", "model": "..."}
```

Group records are append-only with latest-wins by `decided_at`, mirroring
`classifications_as_of`. Keep `Item.cluster_id` as the fast heuristic hint; the group
store is the authority the UI reads. A group whose membership changes gets a new record,
not an edit — same invariant as the rest of the store.

Keep `decided_by` and `confidence` so the UI can present heuristic and
model-adjudicated groups differently, and so a bad model run can be filtered out
without a migration.

### 3.4 UI

`templates/page.html` renders one `<article class="card">` per item via the
`render_card` macro (line 547). The minimal change is a group wrapper that renders the
canonical item as the card and collapses the rest:

```
┌──────────────────────────────────────────────────────┐
│ [A]  MIAX Exchange Group — Daylight Saving Time       │
│      LLM group summary…                               │
│      ⌂ MIAX Options · Sapphire · Pearl · Emerald      │
│      #system-status #options                          │
│      ▸ 4 notices across 2 sources        ← disclosure  │
│      score 74.2 · miax_options · deepseek…            │
└──────────────────────────────────────────────────────┘
        expands to a plain list of the other 4,
        each a title + source_id + direct link
```

Specifics worth deciding up front:

- **Score and rank on the canonical item**, not the group, so ranking stays comparable
  with ungrouped cards. Do not sum scores across members — a 5-way cross-post is not
  five times as important, which is the entire point.
- **Show the affected markets on the card** (decision 3). The data supports this
  directly: each member already carries a per-market `venue`, so the group yields
  `MIAX Options · Sapphire · Pearl · Emerald` without any new extraction. Two wrinkles
  — NYSE already slash-joins several venues into one string
  (`NYSE / NYSE Arca Equities / NYSE Texas`), so split on `" / "` and union before
  rendering; and a wide group needs truncation (`+3 more`) or it will dominate the card.
  Render these as distinct chips, visually separate from tag chips, so the filter bar
  is not confused with market coverage.
- **Union the tags** across members for filtering, so a filter match on any member
  surfaces the group. The existing filter bar reads `data-tags` on the card; the
  wrapper should carry the union.
- **Suppression**: a group should be suppressed only if *every* member falls below
  `suppress_threshold`, otherwise a high-relevance notice could be hidden because its
  canonical twin scored low.
- **Time-travel scrubber**: the wrapper needs `data-first-seen` set to the *earliest*
  member so a group does not pop in and out as members arrive.
- Degrade to today's behaviour when a group has one member — no wrapper, no disclosure.

The email template (`templates/email.html`) should collapse groups to the canonical
line only, with an "and N more" suffix; it is already deliberately terse.

## 4. Phasing

**Phase 0 — render what already exists.** Group by stored `cluster_id` in
`render_page`, add the wrapper and disclosure to `page.html`. No model, no schema
change, no new store. Delivers most of the exact-title win.

**Phase 1 — fix the cross-run gap.** Cluster new items against the stored window
instead of only against each other. Add the URL-slug-stem rule to candidate
generation. Still no model. This is where the MIAX/Cboe fan-outs actually get caught.

**Phase 2 — group store.** Introduce `data/groups/`, move authority there from
`Item.cluster_id`, keep heuristic decisions with `decided_by: "heuristic"`. Pure
refactor, no behaviour change, but it is the schema the model output needs.

**Phase 3 — LLM adjudication.** Add the batched same-event call over candidate groups
only, writing `decided_by: "llm"` records with confidence. Gate behind a config flag so
a bad model run can be switched off without a redeploy.

Phases 0–2 need no model and are independently useful; Phase 3 is the only part that
needs the LLM, and it is refinement rather than the bulk of the win.

## 5. Risks

- **Over-merging is worse than under-merging.** Collapsing two genuinely different
  notices hides one entirely. Bias thresholds conservative, and prefer showing a group
  of 2 over silently dropping a card. `confidence` in the group record exists so the UI
  can require a floor before collapsing.
- **My normalization above is deliberately aggressive** and over-merges on purpose to
  size the opportunity — it strips market words, so it treats "Options BOEv3" and
  "Equities BOEv3" as one. Those are *different documents*. The 37% figure is from
  exact-title matching only and is sound; the 61% corpus-wide figure is an upper bound,
  not a target.
- **Canonical choice is not obvious** for operational notices. `_pick_canonical` prefers
  Federal Register for filings and otherwise takes `group[0]`, which is arbitrary
  ordering. Worth an explicit source-priority list in `config.yaml`.
- **Groups are not stable across runs** unless `group_id` is derived from content
  (e.g. hash of sorted member ids) or persisted. Unstable ids will break the UI's
  time-travel scrubber and any future per-group read state.

## 6. Decisions

Resolved 2026-08-30.

**1. Grouping stays inside one exchange group — no cross-venue-group merging.**
Judged unlikely to arise in practice, and materially harder to get right. This makes
the venue guard a *narrowing* problem rather than a removal one: keep the guard, but
compare exchange groups instead of per-market venues (§3.2). It also removes the
riskiest over-merge case, since two different exchanges announcing the same SEC-driven
change will simply stay as separate cards.

**2. The LLM writes a purpose-built group summary** rather than reusing the canonical
item's. Cost is negligible at ~5 batched calls per run. The constraint is freshness:
key the summary on the membership hash so it is not regenerated on every run (§3.2).
This is a second surface that can go stale independently of `Classification` — an
argument for keeping `decided_by`/`confidence` on the record so a bad batch can be
filtered rather than migrated.

**3. The card shows the list of affected markets.** Already supported by the stored
data — `venue` is per-market, so a group naturally yields its market list (§3.4). This
also makes the collapse legible: "4 notices" says little, "MIAX Options · Sapphire ·
Pearl · Emerald" says exactly what was merged and lets a reader spot a bad merge at a
glance.

## 7. Remaining open question

Is a 4-way MIAX cross-post ever worth showing as four cards — is per-market
confirmation itself signal to an options market-making desk? Decision 3 partly hedges
this: the market list on the collapsed card preserves the coverage information without
the four-card cost. If that proves insufficient, the disclosure expansion is the escape
hatch.
