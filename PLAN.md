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

**Runtime dependencies (keep minimal):** `httpx` (HTTP with timeouts/retries), `feedparser` (RSS/Atom — removes a pile of edge cases), `PyYAML` (config), `Jinja2` (page + email templates), `selectolax` (fast CSS-selector HTML parsing for scrapers), `pypdf` (PDF text extraction, e.g. BOX circulars via `ingest/pdf_text.py`). Everything else is stdlib (`imaplib`, `email`, `smtplib`, `json`, `hashlib`, `logging`, `datetime`, `argparse`). Tests use `pytest`.

**Cross-cutting, done alongside every step (not after):** record a sample input per adapter (feed XML, email `.eml`) and unit-test parsing offline; use a fake `LlmClient` for deterministic pipeline tests; keep one golden-file test for the renderer. Per-source health and run counters are written from day one (§10).

---

## Phase 0 — Scaffold and contracts ✅

**0.1 Repo and layout.** ✅ `src/sweepreader/{ingest,classify,render,store,cli}/`, `config.yaml`, `data/`, `tests/`, `.github/workflows/`, `pyproject.toml`. Python 3.13 (3.12 unavailable on dev machine — divergence from spec; CI will use 3.12 on ubuntu-latest).
**Done when** `python -m sweepreader --help` lists subcommands (`run`, `email`, `backtest`). ✓

**0.2 Config loader.** ✅ `src/sweepreader/config.py` — `AppConfig`/`SourceConfig` dataclasses, YAML validation with clear error messages, `config_hash()` for classification versioning.
**Done when** a malformed config raises a clear error and the sample config loads into objects. ✓ (7 tests pass)

**0.3 Data model and store.** ✅ `src/sweepreader/store/models.py` (`Item`, `Classification`), `src/sweepreader/store/store.py` (`Store`, `StateStore`). Month-sharded JSONL, dedup-on-append, raw_text capped at 8000 chars (~2k tokens).
**Done when** a unit test appends items twice and the second append adds zero duplicates and does not touch the prior month's shard. ✓ (7 tests pass)

**0.4 Hello-world deploy.** ✅ `.github/workflows/deploy.yml` (page rebuild + Pages deploy) and `.github/workflows/email.yml` (10:13 UTC cron). Placeholder `docs/index.html` committed.
**Done when** a manually-dispatched workflow publishes a live Pages URL. ⚠️ *Requires repo to be pushed to GitHub and Pages enabled — not verifiable locally.*

---

## Phase 1 — Structured core ✅

**1.1 Federal Register adapter.** ✅ `src/sweepreader/ingest/federal_register.py` — paginates to 14-day lookback, stable IDs from document_number, venue extraction from filing numbers/title patterns. `ingest/base.py` defines `BaseAdapter` + `fetch_source()`.
**Done when** a run pulls recent SRO notices (count > 0) and re-running produces stable ids and zero new items. ✓ (fixture tests pass)

**1.2 LLM interface + OpenRouter client.** ✅ `src/sweepreader/classify/classifier.py` — `LlmClient` ABC, `OpenRouterClient` with JSON extraction (handles markdown fences), retry on malformed JSON, `keyword_fallback` for when LLM fails. Config hash from profile+weights+threshold.
**Done when** a known item returns a valid `Classification`; a forced bad response triggers the retry then keyword fallback. ✓ (6 tests pass)

**1.3 Classification cache.** ✅ Built into `Store.has_classification(item_id, model, config_hash)` — checked before every LLM call in `cli/run.py`.
**Done when** a second run over the same items makes zero LLM calls. ✓

**1.4 Scoring, ranking, suppression.** ✅ `src/sweepreader/score.py` — exponential decay with 7-day half-life. 6 tests confirm D/B ordering and E suppression.
**Done when** unit tests confirm ordering. ✓

**1.5 Page renderer.** ✅ `templates/page.html` + `src/sweepreader/render/page.py` — tier rail + relevance meter cards, New today/Earlier/Suppressed sections, time-travel scrubber (client-side), dark/light theme via CSS variables + localStorage. **MILESTONE ✓**

**1.6 Email renderer + sender.** ✅ `templates/email.html` + `src/sweepreader/render/email_render.py` — delta since `last_email_sent_at`, top A/B + also-look list + suppressed count + View live link. `--dry-run` prints HTML. **MILESTONE ✓**

**1.7 Backtest CLI.** ✅ `src/sweepreader/cli/backtest.py` — reads range, classifies uncached items, prints ranked top-20 with scores.
**Done when** unchanged config costs zero tokens. ✓

**1.8 Breadth + clustering.** ✅ Generic RSS adapter (`ingest/rss.py`), all Tier-1 sources in `config.yaml` (Cboe, Nasdaqtrader, OCC, CAT, FINRA, SEC, MEMX). Clustering in `ingest/cluster.py` — filing-number match + title-similarity fallback, Federal Register preferred as canonical.

**1.9 GitHub Actions automation.** ✅ `deploy.yml` (cron `7 */3 * * *`), `email.yml` (cron `13 10 * * *`), `ci.yml` (tests on push/PR). Failure flagging via exit-nonzero step after deploy. Concurrency groups prevent overlap.

---

## Phase 2 — Email ingestion

**2.1 Stand up the dedicated Gmail (§9).** ⚠️ **Requires manual action** — create Google account, enable 2-Step Verification, generate App Password, enable IMAP. Add secrets `IMAP_HOST=imap.gmail.com`, `IMAP_USER`, `IMAP_PASSWORD` to GitHub repo. Done when a test message is readable over IMAP.

**2.2 EmailIngestor adapter.** ✅ `src/sweepreader/ingest/email_ingestor.py` — IMAP SSL read-only, attributes by Delivered-To, strips HTML→text, advances UID watermark stored in `state.json` under `imap_uid_{source_id}`. IMAP creds read at call time from env. 4 fixture-based tests pass.

**2.3 Subscribe and enable Tier-2 venues.** ⚠️ **Requires manual action** — subscribe `you+miax@`, `you+nyse@`, `you+box@`, `you+iex@`, `you+24x@` to venue mailing lists, then flip `enabled: true` in `config.yaml` for each. Config entries are already present.

---

## Phase 3 — Scrapers (only if a gap remains)

**3.1 Per-venue HTML/PDF adapters** for any technical-notice page not covered by feed or email. One adapter per holdout, each with a recorded fixture and parse test.
**Done when** the targeted venue's bulletins appear as items and the parse test passes against the fixture.

**3.0 Scraping approach.** HTML parsing uses **selectolax** (fast lexbor backend, CSS selectors) via the shared `ingest/html_text.py` helper — not regex. The rule of thumb: *don't parse HTML if a structured endpoint exists.* Where a site has a JSON/API backend we hit it directly (NYSE); CBOE's table lives in a Next.js flight payload we reassemble then parse; only genuine HTML listings (MIAX) are scraped with CSS selectors. Avoided: Scrapy (framework overkill) and Playwright/Selenium (headless browser — unnecessary since content is server-rendered or API-backed).

**3.1a MIAX alerts scraper.** ✅ `src/sweepreader/ingest/miax.py` (`parse: miax_alerts`, `modality: scrape`). MIAX exposes no feed but its alert listings are plain server-rendered Drupal HTML. The adapter reads each listing page (`miax_options`/`miax_equities`/`miax_futures` in `config.yaml`), extracts per-alert URL + date (from the `/alert/YYYY/MM/DD/<slug>` path) + venue + alert-type + title via selectolax, then fetches each alert's detail page for the full body. Per-item isolation: a failed detail fetch falls back to header-only text. Repeated per-venue cross-posts share a title and merge via existing clustering. Listings paginate with `?page=N` (newest first), walked by the seed CLI. Supersedes the Tier-2 `email_miax` path (kept disabled as a fallback).

**3.1b NYSE Trader Updates adapter.** ✅ `src/sweepreader/ingest/nyse.py` (`parse: nyse_notifications`, `modality: api`, source `nyse_trader_updates`). The `nyse.com/trader-update/history` list renders client-side from a public, paginated, date-sorted notifications JSON API (`/api/notifications/public/system/1/summaries/filter`) discovered behind the page's `notification-history-2023` CMS component. Each record carries `subject`, full HTML `body` (stripped via selectolax), epoch-ms `publishedDate`, and `marketLinks`/`serviceLinks`, so no per-item detail fetch is needed. ~18.5k records reach back to 2006. (NYSE *rule filings* still arrive via the Federal Register; this adds the operational/technical bulletins.)

**3.1c BOX notices scraper (PDF).** ✅ `src/sweepreader/ingest/box.py` (`parse: box_notices`, `modality: scrape`). BOX migrated to WordPress; the `/notices` listing is the only place carrying each circular's number, category, and "View Document" **PDF link** (the RSS feed is title-only and single-notice pages render an empty body). The listing sits behind a Cloudflare *header* gate — not an interactive captcha — so a full browser `sec-ch-ua`/`Sec-Fetch-*` header set passes it. The adapter parses each `<article class="circulars">` row (selectolax) for title, circular #, date, categories (`notice_category-*` classes), and the PDF URL, then extracts the PDF text via the shared `ingest/pdf_text.py` (pypdf) — yielding the real notice body (TO/FROM/SUBJECT + content), not just a headline. On a hard Cloudflare block (e.g. a stricter CI-IP challenge) it falls back to the title-only RSS feed so BOX still produces items. Supersedes the disabled `email_box`. Deep seeding skipped (listing/feed expose only recent items; BOX filing history is in the Federal Register).

**3.1d IEX Trading Alerts adapter.** ✅ `src/sweepreader/ingest/iex.py` (`parse: iex_alerts`, `modality: api`, source `iex_alerts`). IEX's `notifications.iex.io/tradingalerts` (Next.js) loads from a public, paginated JSON API discovered in its JS chunks: `GET api.notifications.iex.io/api/v1/public/trading-alerts?page=N&limit=M`. Items carry `title`, full HTML `content` (stripped via selectolax), `category`, `venue` (Options/Equities), `alert_id`, and ISO `published_at` — content inline, no detail fetch. Per-item pages resolve at `…/tradingalerts/<id>`. Seedable (content inline). Covers IEX Options + Equities; supersedes the disabled `email_iex`.

**3.1e OPRA notices adapter (PDF).** ✅ `src/sweepreader/ingest/opra.py` (`parse: opra_notices`, `modality: scrape`, source `opra_notices`). The opraplan.com homepage lists **every** notice (all years, 2015–present) in one table — each row a date + title + link to the notice PDF on `cdn.opraplan.com/documents/notices/`. So a single page fetch covers both the live window and full history (no pagination); the PDF body is extracted via the shared `ingest/pdf_text.py`. Fully seedable. Covers OPRA feed-product/data-distribution changes (SPEC §2 Tier-2). Added `HttpCache.fetch_bytes` so seeded PDFs are cached as binary.

**3.2 Historical seed CLI (backtesting).** ✅ `sweepreader seed [--months 6] [--source ...]` (`cli/seed.py`). Pages each seedable source's history back to the cutoff and appends to the append-only store with `first_seen_at = published_at`, so the seeded past reconstructs faithfully under time-travel and backtest (SPEC §5). Idempotent (re-runs add zero). Classification is left to `run`/`backtest` so only uncached combos cost tokens. **Federal Register** (`fed_register_sro`) is the deepest seed — its JSON API already date-paginates, so `iter_seed_items` just bounds the query with `publication_date >= cutoff` (years of SRO rule filings; a multi-year seed beyond ~2000 results would need date-window chunking). NYSE/IEX seed from their APIs (bodies inline). MIAX walks `?page=N` (≈1.5 pages/day) using **teaser-first + lazy body**: each item starts from the listing teaser and a detail page is fetched only when a cheap token-free keyword gate accepts it (tier-E noise stays teaser-only) — in a 5-day sample this skipped ~60% of detail fetches. Flags: `--all-bodies`, `--body-min-relevance`, `--no-cache`.

**3.3 HTTP fetch cache.** ✅ `ingest/http_cache.py` — content-addressed, gzipped responses under a gitignored `.cache/http`, keyed by URL+params. Separates fetch from parse (bronze→silver): seeds are resumable/idempotent at the network layer and re-parsing costs no network, without bloating the git-committed store (which keeps only capped extracted text). Used by the seeder for both sources; live `run` stays cacheless (listings change). 3 tests.

---

## Phase 4 — Feedback loop (removed)

Thumbs up/down buttons and the `FeedbackStore`/`serve` command were removed to keep the page a pure static artifact with no server dependency.

---

## Suggested sequence at a glance

`0.1 → 0.2 → 0.3 → 0.4 → 1.1 → 1.2 → 1.3 → 1.4 → 1.5 (page MVP) → 1.6 (email MVP) → 1.7 → 1.8 → 1.9 (automated) → 2.1 → 2.2 → 2.3 → [3.1 as needed] → [4.x optional]`

The product is usable and worth living with after **1.6**; automated after **1.9**; fully covered after **2.3**.