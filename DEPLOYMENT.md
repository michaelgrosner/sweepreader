# SweepReader — Deployment Guide

Step-by-step from zero to a running automated digest.

---

## Prerequisites

- `gh` CLI installed and authenticated (`gh auth login`)
- Python 3.12+ available
- A Google account for the dedicated mailbox (created separately from your personal Gmail)

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

# IMAP — same dedicated account
gh secret set IMAP_HOST        --repo michaelgrosner/sweepreader --body "imap.gmail.com"
gh secret set IMAP_USER        --repo michaelgrosner/sweepreader   # same dedicated Gmail address
gh secret set IMAP_PASSWORD    --repo michaelgrosner/sweepreader   # same App Password
```

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

This fetches all Tier-1 sources, classifies new items, commits data shards, and deploys `docs/index.html` to Pages. Expect the first run to take ~2–3 minutes.

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

Use the dedicated Gmail's `+tag` subaddresses. Emails to these addresses land in the same inbox; the `+tag` part is preserved in the `Delivered-To` header and used for source attribution.

| Source | Subscribe address | Subscription page |
|---|---|---|
| MIAX | `yourname.sweepreader+miax@gmail.com` | miaxglobal.com → each market's Alerts page |
| NYSE | `yourname.sweepreader+nyse@gmail.com` | nyse.com/markets/notices |
| BOX | `yourname.sweepreader+box@gmail.com` | boxoptions.com/circulars |
| IEX | `yourname.sweepreader+iex@gmail.com` | iextrading.com/alerts |
| 24X | `yourname.sweepreader+24x@gmail.com` | 24xnational.com (contact/press) |

After confirming subscription emails arrive, enable each source in `config.yaml`:

```yaml
# Change:   enabled: false
# To:       enabled: true
```

Then push:
```bash
git add config.yaml
git commit -m "enable Tier-2 email sources: miax nyse box iex 24x"
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
source .env

python -m sweepreader run            # fetch + classify + render page locally
python -m sweepreader email --dry-run  # preview digest HTML
```

The `data/` directory accumulates locally the same way it does in CI. Run `git add data/ && git push` to sync local data back to the repo if you've been developing locally.
