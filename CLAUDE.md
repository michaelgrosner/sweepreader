# SweepReader

Python 3.12 pipeline that ingests exchange/regulator notices, classifies them with
an LLM via OpenRouter, and renders a static page to `docs/` for GitHub Pages.

Note: the global instructions describe C#/.NET HFT work. That does not apply here —
this project is Python, and readability matters more than micro-optimization. The
pipeline runs every 3 hours in CI, not on a hot path.

## Git workflow

**Commit directly to `main`. Do not create branches.**

This is a single-maintainer repo and the scheduled `Rebuild Page` workflow commits
data shards straight to `main` every 3 hours. Feature branches add no review value
here and only create divergence against the bot's commits.

- Do not open PRs. Do not create worktrees.
- Do not push unless explicitly asked.
- Prefer several small, logically-separated commits over one large one. Keep
  mechanical data-shard churn in its own commit, apart from source changes.
- `git pull --ff-only` will usually fail because of bot commits; rebase local work
  on top with `git pull --rebase`.

## Time and testing

The ingest adapters and the renderer filter on a lookback window. **Never read
`datetime.now()` directly inside `fetch()` or a render function** — take an
injectable `now` instead:

- adapters: call `self._clock()` (see `BaseAdapter`), never `datetime.now()`
- `render_page(config, store, state, now=None)`

Tests pin the clock to `FIXTURE_NOW` in `tests/conftest.py`. This exists because
the whole suite silently rotted into failure once the static fixtures aged past
the 14-day window. Do not "fix" a time-dependent test by refreshing fixture dates;
pin the clock instead.

Run tests with `python -m pytest`.

## Classification cache

`config_hash()` deliberately excludes the model id, and the cache key is
`(item_id, config_hash)`. Changing models must not invalidate stored
classifications. The model is recorded on each record as provenance only.

Anything added to `config_hash()` invalidates all 4,900+ stored classifications and
forces a full re-run against the LLM. Think before adding a field. Note that the
prompt built in `classify/classifier.py` is *not* covered by the hash — only
`profile_prompt` from `config.yaml` is.

`data/classifications/*.jsonl` is append-only (see `store.append_classification`).
Rewriting it in place is acceptable only for a deliberate one-time migration.

## Static analysis

None is configured; dev deps are pytest only. mypy reports 7 pre-existing
errors in src/ (score.py, federal_register.py, email_ingestor.py, box.py). If
you add a checker to CI, baseline those first or every run will fail. The
box.py:172 report is a false positive — mypy cannot prove a sliced struct_time
has length 6.
