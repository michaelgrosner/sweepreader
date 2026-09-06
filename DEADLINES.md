# Deadline extraction — spec

Status: **implemented.** Spans the classifier schema, the store,
the renderer and two templates (page and email).


## Why

The tag vocabulary already has `cert-window`, `deadline` and `action-required`,
so an item can be marked as time-critical — but carries no *date*. There is no
way to sort by urgency, warn on something closing in three days, or notice that
a certification window opened while the reader was on holiday. For a reader
whose expensive failure mode is missing a cutover date, the tag alone is the
least useful half of the signal.

## What it is not

Not a calendar of the item's own publication date, and not the effective date of
every rule filing (nearly all of them have one, so it would tag ~everything and
mean nothing). Scope is dates the reader must *act* before:

- certification / testing windows (open and close)
- production cutover or migration dates
- mandatory upgrade-by dates for a protocol or spec version
- comment-period closes on filings that affect market-making obligations
- retirement of a feed, port, protocol version or symbology scheme

## Data model

Two nullable fields on `Classification` (`store/models.py`):

```python
deadline_date: Optional[date] = None      # the date itself
deadline_kind: Optional[str] = None       # closed vocabulary, see below
```

`deadline_kind` vocabulary, mirroring the `tags.py` pattern with a
`sanitize_deadline_kind()` that filters to the closed set:

    cert-window-opens  cert-window-closes  cutover  upgrade-by
    comment-closes     retirement

Both fields round-trip through `to_dict`/`from_dict` with a back-compat default
of `None`, exactly as `tags` did.

### Cost: no re-classification

`config_hash()` covers only `suppress_threshold`, `profile_prompt` and
`tier_weights`, and the prompt built in `classify/classifier.py` is deliberately
**not** hashed. Adding these fields therefore does not invalidate any of the
~4,900 stored classifications: existing records load with `deadline_date=None`
and are never re-sent to the LLM. Only newly-classified items get dates, and the
page backfills naturally as the window rolls forward.

This is the same trade `tags` made (see FUTURE.md). Accept the same consequence:
historical items show no deadline until a deliberate re-run.

**Do not** add either field to `config_hash()`.

## Classifier changes

`_CLASSIFY_SCHEMA` gains:

```python
"deadline_date": {"type": ["string", "null"]},   # ISO 8601 date, or null
"deadline_kind": {"type": ["string", "null"]},
```

Both stay out of `required` — the overwhelming majority of items have no
deadline, and forcing the key invites invention.

Prompt addition, placed with the other config-static content **before** the
per-item block so the cacheable prefix is not broken:

> `deadline_date`: if the text states a date the reader must act before —
> certification window open/close, cutover, mandatory upgrade-by, comment-period
> close, or retirement of a feed/port/protocol — return it as `YYYY-MM-DD`.
> Otherwise return null. Do not infer, extrapolate, or convert a relative phrase
> ("in 30 days") into a date; return null unless an explicit calendar date is
> present in the text. The item's own publication date is never a deadline.

The "do not infer" clause is the load-bearing one. A hallucinated cutover date is
worse than no date at all, because it will be trusted.

### Validation

Extend `_validate_response()`:

- `deadline_date` must be `None` or parse under `date.fromisoformat`; anything
  else → treat the field as `None` rather than failing the whole response (a bad
  date should not cost a usable classification).
- `deadline_kind` must be `None` or in the closed set; otherwise `None`.
- Sanity bound: reject dates more than 2 years past `item.published_at` or more
  than 5 years after it. Both directions occur — models echo a date from a
  quoted historical rule, or typo the year.
- If `deadline_date` is set but `deadline_kind` is not, keep the date and leave
  the kind `None`; the UI must tolerate it.

`keyword_fallback()` returns `None` for both. Regex-dating notice text without
the model is not worth the false-positive rate.

## Ranking

Deliberately **no** change to `score.py` in v1.

An imminent deadline is a presentation concern, not a relevance one — a fee
filing closing tomorrow is still a fee filing. Boosting score on proximity would
smuggle urgency into a number the page explains as
`relevance × tier_weight × decay`, and that explanation is printed on every card.
Revisit only if the rail below proves insufficient.

## Page

**A pinned rail above "New today"**, listing items with a `deadline_date` in
`[today - 1d, today + 45d]`, ascending by date, independent of the trailing
window — a cert window closing in a week must not vanish because the notice
announcing it is 15 days old. This means `render_page()` must query the store
beyond `trailing_days` for dated items; cap the lookback at `max_age_days`.

Each row: date, days-remaining, kind, venue, title, link to the full card via the
`#item-<id>` permalink added in the URL-state work.

Urgency styling by days remaining: `<= 3` red, `<= 14` amber, else neutral. Past
dates within the 1-day grace window render struck through and drop off after.

On the card itself: a chip next to the tag chips, `⏱ cert-window-closes ·
2026-10-14 · 12d`, using the existing `.tag-chip` styling with an urgency colour.

Filter integration: a `deadline` toggle in the scrubber row beside `NEW`, and a
`deadline=1` query param, following the pattern already established there.

## Email digest

`render_email()` gains the same rail, above the A/B items, restricted to
`<= 14d`. It is the highest-value block in the digest and should not sit below
the fold. No change to the "Also worth a look" list.

## Tests

Pin the clock to `FIXTURE_NOW` as everywhere else — never refresh fixture dates.

- schema round-trip: `to_dict`/`from_dict` with both fields set and both absent
- absent fields on a stored record load as `None` (back-compat)
- `config_hash()` is unchanged by adding the fields — assert explicitly, this is
  the regression that would cost a full re-run
- validation: bad ISO string, out-of-range year, unknown kind, kind-without-date
- rail selection: an item outside `trailing_days` but with a near deadline is
  included; one with a deadline 90 days out is not
- urgency buckets at the 3 / 14 / 45 day boundaries, and the past-date grace day

## Rollout

1. Model + store + validation, no UI. Ship it and let the fields populate.
2. After ~2 weeks, audit stored dates against their source notices by hand. The
   whole feature rests on the model not inventing dates; measure that before
   putting a red "3 DAYS" badge in front of anyone.
3. Only then the rail, card chip, filter, and digest block.
