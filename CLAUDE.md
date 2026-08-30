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

CI runs ruff and mypy before the tests; all three must pass. Run them locally
the same way — both read their config from `pyproject.toml`, so pass no flags:

```
python -m ruff check      # add --fix for the mechanical ones
python -m mypy
python -m pytest
```

`src/` and `tests/` are currently clean under `check_untyped_defs`. Keep them
that way rather than adding blanket ignores. Two targeted `# type: ignore`
comments exist, both for typeshed imprecision rather than real defects, and
`warn_unused_ignores` is on so they will error if they ever stop being needed.

Ignored ruff rules are stylistic only (E501 line length, E702 semicolons,
E731 lambda assignment). Do not widen that list to silence a correctness rule.

## API design

Identifiers that are structurally interchangeable — several `str` parameters
in a row — must be keyword-only, as on `Store.classifications_as_of` and
`Store.has_classification`. A type checker cannot distinguish `str` from `str`,
so positional passing of `config_hash`/`model`-shaped values is a silent
failure mode. Keyword-only turns it into an error at every call site.
