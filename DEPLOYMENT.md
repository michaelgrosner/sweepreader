# SweepReader — Deployment Guide

Step-by-step from zero to a running automated digest.

---

## Prerequisites

- `gh` CLI installed and authenticated (`gh auth login`)
- Python 3.12+ available
- A Google account for the dedicated mailbox — needed to **send** the daily digest (SMTP) and to receive the remaining email-only venue (24X). MIAX, NYSE, BOX, and IEX no longer need email; they're scraped/API-fetched automatically.

Dependencies install from `requirements.txt` (`httpx`, `feedparser`, `PyYAML`, `Jinja2`, `selectolax`, `pypdf`). All ship prebuilt wheels, so no compiler is required.

---

## 1. Push to GitHub

```bash
gh repo create sweepreader --private --source=. --push
```

Use `--public` instead of `--private` if you want the page publicly accessible — the spec says public is fine since it only contains public regulatory content.

---

## 2. Enable GitHub Pages

```bash
gh api repos/michaelgrosner/sweepreader/pages \
  --method POST \
  -f build_type=legacy \
  -f source='{"branch":"main","path":"/docs"}'
```

If that returns a 409 (already exists), go to:
`https://github.com/michaelgrosner/sweepreader/settings/pages`
and set Branch = `main`, Folder = `/docs` manually.

Your page URL will be: **https://michaelgrosner.github.io/sweepreader/**

---

## 3. Get an OpenRouter API key

1. Sign in at https://openrouter.ai/
2. Go to **Keys → Create Key**
3. Copy the key — you'll use it in Step 5

---

## 4. Set up the dedicated Gmail account

This account both **receives** venue alert emails (IMAP) and **sends** the daily digest to your personal email (SMTP). You only need one account and one App Password.

**Create the account:**
1. Go to https://accounts.google.com/signup
2. Create a new account, e.g. `yourname.sweepreader@gmail.com`
3. Use your personal email/phone as recovery

**Enable IMAP:**
1. Open Gmail with the new account
2. Settings (gear) → See all settings → **Forwarding and POP/IMAP** tab
3. Under **IMAP access**: select **Enable IMAP** → Save

**Generate an App Password:**
1. Go to https://myaccount.google.com/security (logged in as the new account)
2. Enable **2-Step Verification** if not already on
3. Search for "App passwords" in the account settings search bar
4. App name: `SweepReader` → **Generate**
5. Copy the 16-character password shown (no spaces) — this is used for **both** SMTP and IMAP

**Add a spam-exception filter** so venue emails aren't filtered:
1. Gmail → Settings → Filters and blocked addresses → **Create a new filter**
2. In **To**: `yourname.sweepreader@gmail.com`
3. Click **Create filter** → check **Never send it to Spam** → Save

---

## 5. Add GitHub Secrets

Run each of these (replace values with yours):

```bash
# LLM
gh secret set OPENROUTER_API_KEY --repo michaelgrosner/sweepreader

# SMTP — dedicated account sends, your personal email receives
gh secret set SMTP_HOST        --repo michaelgrosner/sweepreader --body "smtp.gmail.com"
gh secret set SMTP_USER        --repo michaelgrosner/sweepreader   # dedicated Gmail address
gh secret set SMTP_PASSWORD    --repo michaelgrosner/sweepreader   # App Password from Step 4
gh secret set SMTP_TO          --repo michaelgrosner/sweepreader --body "your.personal.email@example.com"

# IMAP — same dedicated account (only used by the remaining email venue: 24X)
gh secret set IMAP_HOST        --repo michaelgrosner/sweepreader --body "imap.gmail.com"
gh secret set IMAP_USER        --repo michaelgrosner/sweepreader   # same dedicated Gmail address
gh secret set IMAP_PASSWORD    --repo michaelgrosner/sweepreader   # same App Password
```

The `IMAP_*` secrets are only consumed once you enable a Phase-2 email source (§7); MIAX, NYSE, BOX, and IEX are now covered by the scrape/API adapters, so you can defer them if you're not wiring 24X yet.

`gh secret set` without `--body` will prompt you to type the value (not echoed).

Verify all 8 secrets are present:
```bash
gh secret list --repo michaelgrosner/sweepreader
```

---

## 6. Verify the pipeline

**Trigger a full page rebuild:**
```bash
gh workflow run "Rebuild Page" --repo michaelgrosner/sweepreader
gh run watch --repo michaelgrosner/sweepreader
```

This fetches all enabled sources — Federal Register, Cboe (with revision-history enrichment), Nasdaqtrader, OCC, CAT, FINRA/SEC, MEMX, the **BOX notices scraper** (PDF circulars), the **OPRA notices scraper** (PDF), the **MIAX alert scrapers**, the **NYSE Trader Updates API**, and the **IEX Trading Alerts API** — classifies new items, commits data shards, and deploys `docs/index.html` to Pages. Expect the first run to take ~2–3 minutes.

> **BOX + Cloudflare note:** the BOX `/notices` listing is behind a Cloudflare header gate that the adapter passes with browser headers from a normal IP. GitHub Actions datacenter IPs *may* draw a stricter challenge; if so, the adapter automatically falls back to the title-only BOX RSS feed (you'll still get BOX items, just without the PDF body). Watch the `box_notices` source-health line after the first CI run.

Check the live page: https://michaelgrosner.github.io/sweepreader/

**Test email delivery (dry-run first):**
```bash
gh workflow run "Email Digest" --repo michaelgrosner/sweepreader -f dry_run=true
gh run watch --repo michaelgrosner/sweepreader
```

Review the HTML printed in the workflow log. If it looks correct:
```bash
gh workflow run "Email Digest" --repo michaelgrosner/sweepreader
```

The digest should arrive at `your.personal.email@example.com` within a minute.

---

## 7. Subscribe to Tier-2 venue email lists (Phase 2)

> **MIAX, NYSE, BOX, and IEX are already covered automatically** — MIAX by the alert scrapers (`miax_options`/`miax_equities`/`miax_futures`), NYSE by the Trader Updates API (`nyse_trader_updates`), BOX by the notices/PDF scraper (`box_notices`), and IEX by the Trading Alerts API (`iex_alerts`), all enabled by default. No subscription needed. The disabled `email_miax`/`email_nyse`/`email_box`/`email_iex` config entries remain only as a fallback. The email path below is just for the venue with no feed/API.

Use the dedicated Gmail's `+tag` subaddresses. Emails to these addresses land in the same inbox; the `+tag` part is preserved in the `Delivered-To` header and used for source attribution.

| Source | Subscribe address | Subscription page |
|---|---|---|
| 24X | `yourname.sweepreader+24x@gmail.com` | 24xnational.com (contact/press) |

After confirming subscription emails arrive, enable each source in `config.yaml`:

```yaml
# Change:   enabled: false
# To:       enabled: true
```

Then push:
```bash
git add config.yaml
git commit -m "enable Tier-2 email sources: 24x"
git push
```

---

## 8. Ongoing operations

**The pipeline runs automatically:**
- Page rebuild: every 3 hours at minute 7 (`7 */3 * * *`)
- Email digest: daily at 10:13 UTC / ~6am ET (`13 10 * * *`)

**Check health:**
```bash
# See recent workflow runs
gh run list --repo michaelgrosner/sweepreader --limit 10

# View a failed run's logs
gh run view <run-id> --repo michaelgrosner/sweepreader --log-failed
```

**Source health is also visible** on the page footer and as a GitHub Actions failed-run email if `failures_this_run > 0`.

**To seed history for backtesting:** the API/scrape sources expose deep history (Federal Register rule filings years back; NYSE to 2006; MIAX/IEX via paged listings/APIs). Backfill it once into the append-only store so backtests have something to score. Seeded items get `first_seen_at = published_at`, so time-travel reconstructs the past faithfully.
```bash
source .env
python -m sweepreader seed --months 6                 # all seedable sources (Federal Register + NYSE + MIAX + IEX + OPRA)
python -m sweepreader seed --months 6 --source fed_register_sro   # just one source
```
- No `OPENROUTER_API_KEY` needed — `seed` only fetches/stores; classification happens later in `run`/`backtest` (only uncached items cost tokens).
- **Federal Register, NYSE, IEX** are fast (paginated JSON APIs, content inline). **OPRA** is a single homepage fetch (all notice history) plus one PDF per notice. **MIAX** walks `?page=N` and uses *teaser-first + lazy body*: it fetches a full alert page only for items that pass a cheap keyword relevance gate (tier-E noise stays teaser-only), so a 6-month seed skips most detail fetches. Tune with `--all-bodies` (fetch everything) or `--body-min-relevance N`.
- Not seedable: the plain-RSS sources (OCC, CAT, Nasdaqtrader, SEC, MEMX) expose only a recent window, and BOX's listing/feed likewise (BOX filing history is in the Federal Register).
- Responses are cached under `.cache/http` (gitignored), so a re-run or an interrupted seed resumes without re-downloading. `--no-cache` disables it.
- After seeding locally, `git add data/ && git push` to sync the new shards into the repo.

**To tune ranking:** edit `profile_prompt` or `tier_weights` in `config.yaml`, push, then backtest:
```bash
source .env
python -m sweepreader backtest --from 2026-06-01 --to 2026-06-20 --config config.yaml
```

---

## Local secrets setup

```bash
cp .env.example .env
# Edit .env with real values
set -a && source .env && set +a   # export vars so Python subprocess sees them

python -m sweepreader run            # fetch + classify + render page locally
python -m sweepreader email --dry-run  # preview digest HTML
```

The `data/` directory accumulates locally the same way it does in CI. Run `git add data/ && git push` to sync local data back to the repo if you've been developing locally.

The `.cache/` directory (raw HTTP responses used by the scrapers and `seed`) is **gitignored** and local-only — it's a fetch cache, not part of the committed history. Safe to delete anytime to force a refetch.
