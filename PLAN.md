# SweepReader — Implementation Plan

Companion to `sweepreader-spec.md`. The spec is the *what/why*; this is the *how, in what order*. Each step is sized to a sitting or two and ends with a **Done when** check so progress is observable. Section references (§) point at the spec.

## Approach

Build a **walking skeleton first**: one source → classify → rank → deployed page → email, against a single feed, before adding any breadth. That puts a working, testable product on screen by the end of Phase 1.5–1.6; everything after is widening, not de-risking. Two checkpoints are called out as **MILESTONE** below.

**Dev environment.** Local is MacOS; the CI runner is Linux (`ubuntu-latest`). Keep all paths and code OS-agnostic (`pathlib`, no shell-outs).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Runtime dependencies (keep minimal):** `httpx` (HTTP with timeouts/retries), `feedparser` (RSS/Atom — removes a pile of edge cases), `PyYAML` (config), `Jinja2` (page + email templates). Phase 2 adds `pdfplumber`. Everything else is stdlib (`imaplib`, `email`, `smtplib`, `json`, `hashlib`, `logging`, `datetime`, `argparse`). Tests use `pytest`.

**Cross-cutting, done alongside every step (not after):** record a sample input per adapter (feed XML, email `.eml`) and unit-test parsing offline; use a fake `LlmClient` for deterministic pipeline tests; keep one golden-file test for the renderer. Per-source health and run counters are written from day one (§10).

---

## Phase 0 — Scaffold and contracts

**0.1 Repo and layout.** Create the repo and structure: `src/sweepreader/{ingest,classify,render,store,cli}.py`, `config.yaml`, `data/`, `tests/`, `templates/`, `.github/workflows/`. Pin Python 3.12. Add `requirements.txt` and a `__main__.py` entry.
**Done when** `python -m sweepreader --help` lists subcommands (`run`, `email`, `backtest`).

**0.2 Config loader.** Parse `config.yaml` (§3) into typed objects (dataclasses). Validate: tier weights present for A–E, threshold in range, every source has `id`/`modality`/`parse`, ids unique. Fail loudly with a precise message on bad config.
**Done when** a malformed config raises a clear error and the sample config loads into objects.

**0.3 Data model and store.** Implement `Item` and `Classification` (§4) and the append-only store (§5): month-sharded JSONL append (never rewrite a closed shard), read-all-since(`T`), and `state.json` load/save. Stable `Item.id` hashing and dedup-on-append.
**Done when** a unit test appends items twice and the second append adds zero duplicates and does not touch the prior month's shard.

**0.4 (De-risk infra early) Hello-world deploy.** Wire a minimal Actions workflow that publishes a static placeholder page to GitHub Pages using one dummy secret. This proves the runner → Pages → secrets path before any logic depends on it.
**Done when** a manually-dispatched workflow publishes a live Pages URL.

---

## Phase 1 — Structured core

**1.1 Source protocol + Federal Register adapter.** Define `Source.fetch() -> list[Item]`. Implement the Federal Register adapter (§2, `api`): query SEC agency + `self-regulatory`, page results, map to `Item`s, extract `venue` from the title, set `raw_text` truncated to ~2k tokens. Bound the first run to a 7–14 day lookback so you don't ingest years of backlog.
**Done when** a run pulls recent SRO notices (count > 0) and re-running produces stable ids and zero new items.

**1.2 LLM interface + OpenRouter client.** Define `LlmClient.classify(item) -> Classification` and implement `OpenRouterClient` (§7): one structured call, "respond ONLY with JSON", schema-validate the response, retry/repair once on malformed JSON, compute `config_hash` from profile+weights+threshold. Key from `OPENROUTER_API_KEY`.
**Done when** a known item returns a valid `Classification`; a forced bad response triggers the retry then a deterministic keyword fallback flagged `unclassified`.

**1.3 Classification cache.** Before classifying, look up `(item_id, model, config_hash)` in the classifications log; only uncached items hit the LLM; append new results.
**Done when** a second run over the same items makes zero LLM calls.

**1.4 Scoring, ranking, suppression.** Implement `score = relevance × tier_weight × recency_decay` and the suppression rule (below threshold or tier E → link-only). Sort desc.
**Done when** a unit test confirms a high-relevance D item outranks a low-relevance B item, and tier-E items land in the suppressed bucket.

**1.5 Page renderer (time-travel viewer).** Jinja2 template matching the mockup (`sweepreader-mockup.html`, §7): ranked cards with the tier-rail/meter signature, *New today* → *Earlier* → collapsed *Suppressed*, footer health line, light/dark from `prefers-color-scheme` + toggle. Embed recent shards as JSON and implement the client-side as-of filter (default = now).
**Done when** the generated HTML opens locally: items are ranked, the scrubber filters by `first_seen_at`, suppressed items show link-only, dark mode toggles. **MILESTONE: deployable page from one source.**

**1.6 Email renderer + sender.** Jinja2 600px email template (§7): the delta since `state.last_email_sent_at`, top A/B items + *Also worth a look* + suppressed count + "View live" link. Send via `smtplib` (SMTP creds in secrets); support a `--dry-run` that prints instead of sends; advance `last_email_sent_at` only on success.
**Done when** `--dry-run` prints a correct delta and a real send arrives in your inbox. **MILESTONE: end-to-end product on one source.**

**1.7 Backtest CLI.** `backtest --from --to --config candidate.yaml` (§5): read raw items in range, classify any uncached `(item, model, config_hash)`, emit the as-of feed and a diff vs. live config.
**Done when** re-running an unchanged config costs zero tokens and a changed profile yields a visible ranking diff.

**1.8 Breadth — remaining Tier-1 sources.** Add a generic `feedparser`-based RSS adapter and config entries for Cboe (options/equities/futures tech RSS), Nasdaqtrader (selected category feeds + halt/status), OCC, CAT, FINRA, SEC. Implement clustering (§4) so the same filing from Federal Register + a venue feed collapses to one card.
**Done when** each source yields items in a run, and a known duplicated filing renders as a single clustered card with multiple source links.

**1.9 GitHub Actions automation.** Replace the placeholder workflow: page-rebuild cron `7 */3 * * *` and email cron `13 10 * * *` (§8); secrets for OpenRouter + SMTP; steps to run the pipeline, commit `data/` shards, deploy Pages; a `concurrency` group to prevent overlap; optional final step that exits non-zero on `failures_this_run > 0` (§10), ordered after deploy.
**Done when** a scheduled run deploys an updated page and the 10:13 UTC run sends the email; a deliberately broken source still produces a page and flags itself in the health line.

---

## Phase 2 — Email ingestion

**2.1 Stand up the dedicated Gmail (§9).** New account → 2-Step Verification → App Password → enable IMAP → store IMAP secrets. Send yourself a test to `you+test@`.
**Done when** the test message is readable over IMAP from a local script.

**2.2 EmailIngestor adapter.** IMAP read-only, fetch UID > watermark, **attribute by delivered-to address**, strip HTML → text, follow teaser links / extract PDFs (`pdfplumber`), emit `Item`s, advance the watermark idempotently.
**Done when** a message to `you+miax@` becomes an `Item` with `source_id = email_miax`, and re-running reprocesses nothing.

**2.3 Subscribe and enable Tier-2 venues.** Subscribe the Gmail (via `+tag` addresses) to MIAX, NYSE, BOX, IEX, 24X lists; flip their config entries `enabled: true`; add spam-exception filters.
**Done when** each Tier-2 source shows fetches and a green health entry; rule filings still arrive via Federal Register without duplication.

---

## Phase 3 — Scrapers (only if a gap remains)

**3.1 Per-venue HTML/PDF adapters** for any technical-notice page not covered by feed or email. One adapter per holdout, each with a recorded fixture and parse test.
**Done when** the targeted venue's bulletins appear as items and the parse test passes against the fixture.

---

## Phase 4 — Feedback loop (optional)

**4.1 Capture feedback.** Thumbs up/down on each card writes `{item_id, signal, config_hash, ts}` to an append-only `data/feedback/…jsonl`.
**Done when** clicks persist and survive a rebuild.

**4.2 Use it to tune.** Periodically review feedback against the profile prompt and weights; adjust (both data); backtest the change before adopting.
**Done when** a tuning change is justified by a feedback-driven backtest diff, not a guess.

---

## Suggested sequence at a glance

`0.1 → 0.2 → 0.3 → 0.4 → 1.1 → 1.2 → 1.3 → 1.4 → 1.5 (page MVP) → 1.6 (email MVP) → 1.7 → 1.8 → 1.9 (automated) → 2.1 → 2.2 → 2.3 → [3.1 as needed] → [4.x optional]`

The product is usable and worth living with after **1.6**; automated after **1.9**; fully covered after **2.3**.