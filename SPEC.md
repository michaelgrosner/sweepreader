# SweepReader — Implementation Spec

**Audience:** an engineer implementing this from scratch.
**Goal:** a personal webpage plus a once-daily email digest that keeps tabs on technical and market-structure change relevant to a director-level engineer working in US equity-options market making — SEC rule filings, exchange technical/regulatory circulars, and market-structure news. Items are AI-classified, summarized in 2–3 lines, and ranked by personal preference; irrelevant items are suppressed to a link-only list at the bottom. The app is time-aware: it can reconstruct what would have been surfaced at any past date/time.

*(Name: SweepReader — it reads/sweeps across every venue's technical source feed, the way a sweep router sweeps liquidity across venues.)*

**Status of decisions (locked):**
- Language: Python. Runtime: GitHub Actions (scheduled) + GitHub Pages (public is fine — see §11).
- Email digest sends daily at **10:13 UTC**; page rebuilds **every 3 hours**.
- Sorting model: **v1** — LLM assigns relevance + tier + summary per item; final rank is a blended score.
- LLM access via **OpenRouter** behind a provider-agnostic interface.
- **Mailbox: a dedicated Gmail account** (for Tier-2 email-only sources).
- Sources live in the **single app config**, not a separate registry.
- History is **append-only and immutable** (never lose data); time-travel viewing is the default, plus an on-demand re-score backtest.
- Error handling is **minimal and dependency-free** (per-source isolation → Actions log → safe health badge).
- A clickable **UI/email mockup** ships alongside this spec (`sweepreader-mockup.html`); design language in §7.

---

## 1. Architecture overview

A single scheduled batch, five stages plus an append-only history store:

1. **Ingest** — per-source adapters (API / RSS / email) emit normalized `Item` records.
2. **Normalize + dedup** — common schema; cluster items describing the same underlying event.
3. **Classify** — one LLM call per *new* item → relevance, tier, rationale, and (if above threshold) a summary. Cached forever, versioned by config; only new items hit the LLM.
4. **Score + rank** — `final = relevance × tier_weight × recency_decay`.
5. **Render + emit** — rebuild the static page each run; send the email delta once daily.

The page rebuilds more often than the email, so it is fresher; the email is a point-in-time snapshot of what is new since the last send. Both carry a trailing 14-day window. The page also time-travels over the full retained history (§5).

---

## 2. Source catalog (feeds & access)

The organizing fact: **every SRO rule filing — including all new venues — is noticed in the Federal Register, which has a clean JSON API.** So rule filings need zero per-venue work; only each venue's *non-19b-4 operational/technical bulletins* may need email/scrape. Coverage below is split accordingly.

### Tier 1 — RSS / API (no scraping)

| Source | Endpoint / access | Covers |
|---|---|---|
| **Federal Register API** | `https://www.federalregister.gov/api/v1/documents.json` — `conditions[agencies][]=securities-and-exchange-commission`, term `self-regulatory`. JSON, no key. | All SRO 19b-4 notices (every venue) + SEC proposed/final rules |
| **SEC.gov RSS** | Press-release & proposed-rule RSS feeds on sec.gov (verify exact paths). Honor SEC fair-access: declared User-Agent `Name email`, gzip, ≤10 req/s. | SEC press / rulemaking |
| **FINRA** | Regulatory Notices RSS on finra.org | FINRA notices/guidance |
| **Nasdaqtrader News Alerts** | Custom RSS builder `nasdaqtrader.com/trader.aspx?id=newsrss` — select *Options Technical Alerts*, *Options Regulatory Alerts*, *Equity Technical Updates*, *Data Technical News*, *Financial Products News* (down-weight Trader). Plus Trade Halt RSS (`id=TradeHaltRSS`) and System Status RSS (`id=SystemStatusRSS`). The builder emits the actual feed URL from the selected categories. | PHLX, ISE, GEMX, MRX, BX Options, NOM + Nasdaq/BX/PSX equities |
| **Cboe technical RSS** | Per market: `cboe.com/us/options/support/technical/rss`, `cboe.com/us/equities/support/technical/rss`, `cboe.com/us/futures/support/technical/rss` (confirmed live `application/rss+xml`). | Cboe options (C1/C2/BZX/EDGX) + equities (BZX/BYX/EDGA/EDGX) tech specs |
| **OCC (Options Clearing Corp)** | RSS feeds listed at `theocc.com/specialpages/legal/occ-rss-feeds` — OCC Alerts, Bulletins, News Releases, PSAs. (Information Memos are also web-searchable at `infomemo.theocc.com`.) | Clearing alerts/bulletins/memos |
| **CAT NMS** | `https://www.catnmsplan.com/rss/topics.xml` | CAT reporting changes |
| **MEMX** | Notices on `info.memxtrading.com` (category pages, e.g. `/category/alerts-notices/`). Site is WordPress-class, so an RSS feed is likely at the `…/feed/` path — **verify**. | MEMX options + equities |

### Tier 2 — email / scrape (no usable feed found)

Each venue's *rule filings* are already covered by the Federal Register above; only their operational/technical bulletins are listed here. Subscribe the dedicated Gmail (§9) to each.

| Source | Where | Covers |
|---|---|---|
| **MIAX** | Per-market Alerts pages `miaxglobal.com/markets/.../alerts` + email | MIAX Options/Pearl/Emerald/Sapphire + Pearl Equities |
| **NYSE** | Market Notices / Trader Updates `nyse.com/markets/notices` + email | NYSE Arca/American options + NYSE/Arca/American/National/Texas equities |
| **BOX** | Regulatory Circulars + System Alerts `boxoptions.com/circulars/…` + email | BOX Options |
| **IEX** | Alerts `iextrading.com/alerts` + email | IEX equities + IEX Options (at launch) |
| **24X** | Venue notices + email | 24X National Exchange (live) |
| **TXSE** | Press releases (pre-launch) | Texas Stock Exchange (launch 2026) |
| **OPRA** | Feed-change notices via the SIP / exchange technical alerts | Options data feed changes |

Adding a venue at launch (TXSE, IEX Options) is a new config entry, not code. Note from the OCC OLPP roster that one Nasdaq options venue now appears as "Nasdaq Texas" — confirm naming when wiring Nasdaq venues.

---

## 3. Application configuration (single file)

There is **no separate source registry** — sources are one section of the app config (`config.yaml`) alongside weights, the profile prompt, thresholds, and schedule. Adding a venue = adding a list entry.

```yaml
model: "anthropic/claude-haiku-4.5"
suppress_threshold: 35
trailing_days: 14
max_age_days: 183   # hard floor: never ingest/classify/score anything older (required)
profile_prompt: |
  Director-level engineer on US equity-options market-making infrastructure.
  Cares most about: protocol/spec changes, new venues & order types, certification
  windows, feed-product changes, connectivity. Interested in market-structure
  direction. Bored by routine fee filings, halts, corporate actions, M&A.

tier_weights: { A: 1.00, B: 0.85, C: 0.55, D: 0.40, E: 0.10 }

sources:
  - id: fed_register_sro
    modality: api
    endpoint: "https://www.federalregister.gov/api/v1/documents.json?..."
    default_tier_hint: D
    weight: 1.0
    parse: federal_register
  - id: cboe_options_tech
    modality: rss
    endpoint: "https://www.cboe.com/us/options/support/technical/rss"
    default_tier_hint: A
    weight: 1.0
    parse: rss_generic
  - id: email_miax
    modality: email
    address: "you+miax@gmail.com"   # attribution by delivered-to, never From
    default_tier_hint: A
    weight: 0.95
    parse: email_html_or_pdf
    enabled: false                  # Phase 2
```

Email-source attribution keys off the **delivered-to address**, never the From header.

---

## 4. Data model

```
Item {                # immutable once written
  id            # stable hash(source_id + canonical_url || filing_no)
  source_id; venue; title; url
  published_at; first_seen_at   # UTC; first_seen drives time-travel
  raw_text      # body, truncated ~2k tokens for the LLM — kept forever
  modality; cluster_id
}
Classification {      # versioned, append-only — never overwritten
  item_id; model; config_hash; classified_at
  relevance(0-100); tier(A-E); rationale; summary(null when suppressed)
}
```

`score = relevance × tier_weight[tier] × recency_decay(published_at)`, computed at render time from the Classification current as-of the view time.

**Dedup / clustering.** Same event can appear in Federal Register + a venue feed/email. Cluster on shared filing number (`SR-MEMX-2026-02`) when present, else `venue + title-similarity + close timestamps`. Render one card, all source links. Canonical preference: Federal Register for rule filings; the venue's own technical notice for operational/technical items.

---

## 5. Persistence, history, backtesting

- **Items:** append-only JSONL sharded by month — `data/items/YYYY-MM.jsonl`. Past shards never change, so git stores each once. Raw fields never mutated/deleted (satisfies "never lose data" without bloating the repo under 3-hourly commits).
- **Classifications:** append-only, versioned — `data/classifications/YYYY-MM.jsonl`, keyed `(item_id, model, config_hash)`. Re-running under changed config appends, never overwrites.
- **Mutable state:** small `data/state.json` — IMAP UID watermark, `last_email_sent_at`, per-source health, `failures_this_run`, current `config_hash`.
- **Time-travel view (default, not a mode):** a datetime control is always present, default = now. Feed at `T` = items with `first_seen_at ≤ T`, scored with each item's latest Classification `classified_at ≤ T`, within the trailing window relative to `T`. Fully client-side (loads logs as JSON, recent shards eager / older lazy).
- **Backtest (on-demand re-score):** `backtest --from --to --config candidate.yaml` re-classifies preserved raw items under a candidate config; cached `(item, model, config_hash)` combos are free, new ones cost tokens. Reproducible because raw text is kept forever and classifications are content-addressed.

---

## 6. Classification and scoring (v1)

| Tier | Meaning | Examples | Weight |
|---|---|---|---|
| A | Technical / upcoming features | new venues, new order types, protocol/spec changes, certification & test windows, feed-product changes, connectivity/colo, symbology, migrations | 1.00 |
| B | Market-structure news & direction | SEC policy direction, competitive/market-structure developments, structurally notable enforcement | 0.85 |
| C | Exchange operational notices | fee changes, membership/access, system status/incidents, hours/holiday schedules, routine disciplinary actions | 0.55 |
| D | Structural rule filings (MM-affecting) | quoting obligations, 15c3-5, tick size/612, complex orders, PFOF/606, Reg SHO, OCC margin | 0.40 |
| E | Suppressed noise | corporate actions, trading halts, series list/delist, M&A | 0.10 |

Tier is a **prior**, not destiny: a high-relevance D item (Reg SHO change, relevance 95 → 38) outranks a low-relevance B item (40 → 34). One structured LLM call per new item returns JSON `{relevance, tier, venues, rationale, summary}`; instruct it to **omit `summary` below threshold**. Validate against the schema; retry/repair on malformed JSON. Items below `suppress_threshold` (or tier E) render link-only at the bottom. Each card shows its score breakdown so ranking is explainable. Tune by editing the profile prompt and weights (both bump `config_hash`, so changes are versioned and backtestable).

---

## 7. UI / design

A clickable mockup ships with this spec: **`sweepreader-mockup.html`** — self-contained, toggles between the web viewer and the email digest, with a working light/dark switch.

**Design language.** Treat it as a calm instrument panel for someone who reads exchange tech bulletins all day. The **signature element** is a per-item *tier rail + relevance meter* on the left edge of each card: the tier letter (A–E) in its colour, a vertical meter filled to the relevance score, the numeric score beneath — priority and confidence read at a glance, which is the whole product. Everything else stays quiet.

- **Colour:** cool, low-chroma surfaces; a single indigo accent (`#4F46E5` light / `#8B83FF` dark). Tier ramp A→E indigo→blue→teal→amber→grey for fast scanning. Full dark/light via CSS variables on `data-theme`, initialised from `prefers-color-scheme` with a manual toggle.
- **Type:** Space Grotesk for titles/wordmark (characterful, used with restraint), Inter for body/UI, IBM Plex Mono for the data vernacular — venue tags, scores, timestamps, the coverage strip. Mono-for-data matches the world the content comes from.
- **Layout (viewer):** sticky header with wordmark, a faint monospace *coverage strip* (the venue codes being tracked — encodes real coverage, not decoration), the always-present time-travel scrubber defaulting to "now", a source-health pill, and the theme toggle. Body = ranked cards under *New today* → *Earlier · last 14 days* → a collapsed *Suppressed* list (venue + title + link only, no summary). Footer = the non-sensitive health line (last run, counts, any stale source, active model).
- **Layout (email):** a 600px point-in-time frame in the same language — top A/B items with summaries, a compact *Also worth a look* list, a suppressed-count line, and one primary "View live →" button. Dark mode via `prefers-color-scheme` only (many clients strip it; the light treatment is the baseline).

Quality floor: responsive to mobile, visible keyboard focus, reduced-motion respected.

---

## 8. Scheduling and cost

- **Email:** cron `13 10 * * *` (10:13 UTC; avoids the delayed top-of-hour slot; fixed UTC sidesteps DST → lands ~5–6am ET).
- **Page rebuild:** cron `7 */3 * * *`.
- Actions cron is best-effort; the "since last send" design self-recovers from a missed run.
- **Compute: ~$0** (within Actions free allotment). **LLM: a few $/month** on a Haiku-class model — costs are bounded by caching (only new items) and truncating long notices to ~2k tokens, not by model choice. Backtests add tokens only for uncached combos. Verify current rates at build time.

---

## 9. Email ingestion (Phase 2 — dedicated Gmail)

Email is a normal ingestion adapter producing the same `Item` records; only the fetch differs.

**Mailbox setup (decided: dedicated Gmail):**
1. Create a new Google account, separate from personal (its app password goes into CI).
2. Enable 2-Step Verification, then generate an **App Password** (Security → App passwords) for IMAP — never the real password.
3. Enable IMAP in Gmail settings.
4. Subscribe each Tier-2 list using subaddressing — `you+miax@`, `you+nyse@`, `you+box@`, `you+iex@`, … — all land in one inbox with the source tag preserved in the delivered-to header.
5. Add filters so these never route to spam and are labelled by source.
6. Store IMAP host/port (`imap.gmail.com:993`), username, and app password as GitHub secrets.

**EmailIngestor adapter:** connect IMAP read-only; fetch UID > watermark; **attribute by delivered-to address**; strip HTML → text, follow teaser links and extract PDFs (`pdfplumber`/`pypdf`) where the full circular is linked/attached; emit `Item`s; advance the UID watermark (idempotent). Optionally move processed mail to an `Ingested` folder.

**Reliability:** lists silently drop you on bounce, so the per-source health check ("no mail from X in N days → flag") matters most here. Treat the mailbox as a single point of failure and a credential surface.

---

## 10. Error handling and observability (minimal, dependency-free)

- **Per-source / per-item isolation:** wrap each in try/except; one failure never aborts the run. Render + email always proceed with whatever succeeded.
- **Failures → Actions run log** via stdlib `logging` to stderr. The log is private to repo collaborators and Actions auto-masks registered secrets. No tracebacks on the page, nothing sensitive committed, zero dependencies.
- **Page health summary:** the only on-page failure signal is a small block from `state.json` — per-source last-success timestamp and `failures_this_run`. No URLs/tracebacks/raw text.
- **LLM failures:** schema-validate; on repeated failure fall back to deterministic keyword classification (item still appears, flagged "unclassified").
- **Optional active alert (still zero-dep):** a final workflow step exits non-zero when `failures_this_run > 0` → GitHub's built-in failed-run email. Order deploy/commit before it (or `if: always()`) so a flag never blocks delivery.
- No separate error store; `state.json` health + retained Actions logs cover after-the-fact inspection.

---

## 11. Hosting

GitHub Pages. The page carries only public regulatory content and non-sensitive health badges, so **public Pages is fine**; a private repo is an optional preference, not a requirement.

---

## 12. Phased build plan

1. **Phase 1 — structured core (working product):** Federal Register API + Cboe/Nasdaqtrader/OCC/CAT/MEMX RSS + FINRA/SEC RSS → classify via OpenRouter → render page (with time-travel) + send email; append-only sharded history.
2. **Phase 2 — email ingestion:** dedicated Gmail + `EmailIngestor` for MIAX, NYSE, BOX, IEX, 24X.
3. **Phase 3 — scrapers:** any technical-notice pages not covered by feed or email.
4. **Phase 4 — feedback loop (optional):** thumbs up/down → feedback file → prompt/weight tuning.

Backtest (§5) is available from Phase 1 onward.

---

## 13. Open items to confirm / verify at build time

- Confirm the B↔D ordering (market-structure news above MM rule filings); flip weights if not.
- Verify the MEMX `…/feed/` RSS path; confirm whether MIAX/NYSE/BOX/IEX truly lack feeds (then they stay email/scrape).
- Pull the exact Nasdaqtrader RSS builder URLs for the chosen categories, and the exact SEC.gov RSS paths.
- Suppression threshold + recency-decay shape — tune against a few days of real output; backtest before/after.
- "Nasdaq Texas" naming for the relevant Nasdaq options venue.