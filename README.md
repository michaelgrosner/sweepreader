# SweepReader

A personal regulatory feed reader and daily email digest for US equity-options market structure. Sweeps every major venue's technical and regulatory sources the way a sweep router sweeps liquidity across venues.

**What it does:**
- Fetches SEC/Federal Register SRO filings, Cboe/Nasdaq/OCC/FINRA/CAT/MEMX technical RSS feeds, and (Phase 2) venue alert emails
- Classifies each item with an LLM: relevance score 0–100, tier A–E, 2–3 line summary
- Ranks by `relevance × tier_weight × recency_decay` and renders a static page + daily email digest
- Suppresses noise (halts, corporate actions, routine filings) to a collapsed link-only list
- Time-travel scrubber on the page lets you see what was surfaced at any past date

**Live page:** https://michaelgrosner.github.io/sweepreader/

---

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Fill in .env with your keys (see .env.example)
set -a && source .env && set +a   # export vars so Python subprocess sees them

# Fetch sources, classify new items, write data/, rebuild docs/index.html
python -m sweepreader run

# Preview the email digest (prints HTML, no send)
python -m sweepreader email --dry-run

# Re-score a date range under a candidate config
python -m sweepreader backtest --from 2026-06-01 --to 2026-06-20 --config config.yaml

# Run tests
pytest
```

Open `docs/index.html` in a browser after `run` to see the rendered page.

`run` without `--dry-run` writes to `data/` and `docs/index.html`. All data writes are append-only — nothing is ever deleted or overwritten.

---

## Configuration

Everything lives in `config.yaml`:

| Field | Description |
|---|---|
| `model` | OpenRouter model ID (default: `anthropic/claude-haiku-4-5`) |
| `suppress_threshold` | Items below this relevance score are link-only (default: 35) |
| `trailing_days` | Rolling window shown on page and in email (default: 14) |
| `profile_prompt` | Describes the reader — the LLM uses this to calibrate relevance |
| `tier_weights` | A–E weight multipliers applied to the relevance score |
| `page_url` | Deployed Pages URL — used in the "View live →" email link |
| `sources[]` | List of sources; set `enabled: false` to skip a source |

Changing `profile_prompt` or `tier_weights` bumps `config_hash`, so old classifications are preserved and new ones are appended — nothing is recomputed unless you explicitly run `backtest`.

---

## Secrets (GitHub Actions)

| Secret | Value |
|---|---|
| `OPENROUTER_API_KEY` | From openrouter.ai |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_USER` | Dedicated Gmail address (`yourname.sweepreader@gmail.com`) |
| `SMTP_PASSWORD` | App Password from the dedicated account |
| `SMTP_TO` | Your personal email — where the digest is delivered |
| `IMAP_HOST` | `imap.gmail.com` |
| `IMAP_USER` | Same dedicated Gmail address |
| `IMAP_PASSWORD` | Same App Password (or a second one from the same account) |

---

## Architecture

```
config.yaml
    │
    ▼
Ingest (per-source adapters)
  ├── FederalRegisterAdapter  — all SRO 19b-4 filings via JSON API
  ├── RssAdapter              — Cboe, Nasdaqtrader, OCC, CAT, FINRA, SEC, MEMX
  └── EmailIngestor           — Phase 2: IMAP from dedicated Gmail
    │
    ▼  cluster (filing number / title similarity)
    ▼
Classify (OpenRouterClient → Classification)
  └── keyword_fallback if LLM unavailable
    │
    ▼
Store (append-only JSONL, month-sharded)
  ├── data/items/YYYY-MM.jsonl
  ├── data/classifications/YYYY-MM.jsonl
  └── data/state.json  (watermarks, health, last_email_sent_at)
    │
    ▼
Render
  ├── docs/index.html   — rebuilt every 3 hours
  └── email digest      — sent once daily at 10:13 UTC (~5–6am ET)
```

---

## Tiers

| Tier | Meaning | Weight |
|---|---|---|
| A | Protocol/spec changes, new venues/order types, cert windows, feed changes, connectivity | 1.00 |
| B | Market-structure direction, SEC policy, competitive developments | 0.85 |
| C | Exchange operational: fees, membership, system status, holidays | 0.55 |
| D | Rule filings (MM-affecting): quoting obligations, 15c3-5, Reg SHO, tick size | 0.40 |
| E | Suppressed noise: halts, corporate actions, series lists, M&A | 0.10 |

Final score = `relevance (0–100) × tier_weight × exp(−ln2 × age_days / 7)`. Items below `suppress_threshold` or in tier E appear link-only in a collapsed section.

---

## Adding a source

Add an entry to `sources:` in `config.yaml`. No code change needed for RSS or API sources. For a new email source, set `modality: email`, `parse: email_html_or_pdf`, and `address: yourname.sweepreader+venue@gmail.com`.

## Phases

- **Phase 1 (done):** Federal Register + Cboe/Nasdaq/OCC/CAT/FINRA/SEC/MEMX RSS → classify → page + email
- **Phase 2 (done, awaiting Gmail setup):** IMAP email ingestion for MIAX, NYSE, BOX, IEX, 24X
- **Phase 3:** Per-venue HTML scrapers if any gap remains after Phase 2
