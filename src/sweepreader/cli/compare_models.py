"""Compare classifier models on the same items, judged blind by Claude.

Three resumable stages, each reading the previous one's output from --out:

  generate  sample items from the store, send each to every candidate model
            through OpenRouter with the production prompt and request body
  judge     one call per item to a Claude judge via the `claude` CLI, so it
            runs on the Claude Code subscription; candidates are shuffled and
            labelled A, B, C… so neither model name nor position leaks
  report    per-model scores, win rate, validity, cost and latency

The judge sees exactly the prompt each model saw, so a summary is graded on
what the model was given rather than on what the filing might also contain.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import statistics
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from sweepreader.classify.classifier import (
    _OPENROUTER_URL,
    _USER_AGENT,
    _build_prompt,
    _extract_json,
    _validate_response,
    request_body,
)
from sweepreader.config import AppConfig, load_config
from sweepreader.ingest.federal_register import _body_text
from sweepreader.ingest.http_cache import HttpCache
from sweepreader.store import Store
from sweepreader.store.models import Classification, Item

logger = logging.getLogger(__name__)

DEFAULT_MODELS = [
    "deepseek/deepseek-v4.1-flash",
    "anthropic/claude-haiku-5.5",
    "z-ai/glm-5.3-flash",
    "qwen/qwen3.8-flash",
    "~deepseek/deepseek-v4-flash-latest",
]

# Request changes some models need before they will answer at all. Production
# sends request_body() unchanged, so a model that wins here with an override
# needs the same change in classifier.request_body() before it can be adopted.
# A None value removes the key.
_MODEL_OVERRIDES: dict[str, dict] = {
    # Anthropic models on OpenRouter reject temperature on every provider.
    "anthropic/": {"temperature": None},
    # GLM 5.3 refuses reasoning off ("mandatory for this endpoint"); give the
    # reasoning room so it does not starve the answer the way DeepSeek's did.
    "z-ai/glm-5.3": {"reasoning": {"effort": "low"}, "max_tokens": 8192},
}

_SUBSTANTIVE_SHARE = 0.7  # tier-E noise is easy for every model; weight the sample away from it
_MIN_TEXT_CHARS = 150

_FR_HTML_URL = re.compile(r"/documents/(\d{4}/\d{2}/\d{2})/([\d-]+)/")

_JUDGE_SYSTEM = """You are an impartial expert evaluator of LLM outputs for a \
regulatory-notice digest read by a director-level engineer on US equity-options \
market-making infrastructure. Judge only against the source text and \
instructions you are given. Candidate order and labels are random and carry no \
meaning. Longer is not better: a correct one-sentence summary beats a padded \
three-sentence one, and null is the right summary when the item is below the \
stated relevance threshold or the text says nothing beyond its title."""

_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "accuracy": {"type": "integer", "minimum": 1, "maximum": 10},
                    "substance": {"type": "integer", "minimum": 1, "maximum": 10},
                    "classification": {"type": "integer", "minimum": 1, "maximum": 10},
                    "overall": {"type": "integer", "minimum": 1, "maximum": 10},
                    "note": {"type": "string"},
                },
                "required": ["label", "accuracy", "substance", "classification", "overall", "note"],
            },
        },
        "ranking": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["candidates", "ranking"],
}


def _judge_prompt(prompt: str, labelled: list[tuple[str, dict]]) -> str:
    blocks = "\n\n".join(f"### Candidate {label}\n{json.dumps(out, indent=1)}" for label, out in labelled)
    return f"""Each candidate below is a model's JSON response to the classification \
prompt that follows. Score every candidate 1-10 on:

- accuracy: every claim in summary and rationale is supported by the source text; \
no invented dates, rule numbers, venues or effects. Any fabrication caps this at 3.
- substance: the summary leads with what actually changes (rule, field, message \
type, order type, fee, dates) and is useful to the reader. Penalise restating the \
title, generic closers ("firms should review"), and claims about what the text \
does not contain. A null summary is correct when relevance is below the threshold \
or nothing beyond the title can be said, and wrong when there is real substance.
- classification: tier, relevance, venues, tags and deadline fields fit the \
reader profile and tier definitions in the prompt. A deadline_date must be an \
explicit action-by date in the text.
- overall: your holistic judgement of which response a reader would rather have.

Then rank all candidate labels best to worst.

## Classification prompt given to every candidate
{prompt}

## Candidates
{blocks}"""


def _fr_raw_text_url(html_url: str) -> str | None:
    """Federal Register's plain-text URL, derived from the stored html_url."""
    m = _FR_HTML_URL.search(html_url)
    if not m:
        return None
    return f"https://www.federalregister.gov/documents/full_text/text/{m.group(1)}/{m.group(2)}.txt"


def _with_fr_body(item: Item, cache: HttpCache) -> Item:
    """Stored FR items predate body ingest; give them the text production now sends."""
    url = _fr_raw_text_url(item.url)
    if item.source_id != "fed_register_sro" or url is None:
        return item
    try:
        body = _body_text(cache.fetch_text(url))
    except Exception as e:
        logger.warning("FR body fetch failed for %s: %s", item.id, e)
        return item
    return replace(item, raw_text=f"{item.title}\n\n{body}")


def select_sample(items: list[Item], stored: dict[str, Classification], n: int,
                  rng: random.Random) -> list[Item]:
    """Round-robin across sources so no one feed dominates, with most of the
    sample drawn from items previously judged substantive (tier A-D)."""
    def round_robin(pool: list[Item], k: int) -> list[Item]:
        by_source: dict[str, list[Item]] = defaultdict(list)
        for it in pool:
            by_source[it.source_id].append(it)
        queues = [rng.sample(v, len(v)) for _, v in sorted(by_source.items())]
        out: list[Item] = []
        while len(out) < k and any(queues):
            for q in queues:
                if q and len(out) < k:
                    out.append(q.pop())
        return out

    def substantive(it: Item) -> bool:
        cls = stored.get(it.id)
        return cls is not None and not cls.unclassified and cls.tier != "E"

    eligible = [it for it in items
                if it.source_id == "fed_register_sro" or len(it.raw_text) >= _MIN_TEXT_CHARS]
    want = round(n * _SUBSTANTIVE_SHARE)
    picked = round_robin([it for it in eligible if substantive(it)], want)
    picked += round_robin([it for it in eligible if not substantive(it)], n - len(picked))
    return picked


# ── generate ────────────────────────────────────────────────────────────────

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def eval_body(model: str, prompt: str) -> dict:
    body = request_body(model, prompt, max_tokens=2048)
    for prefix, override in _MODEL_OVERRIDES.items():
        if model.lstrip("~").startswith(prefix):
            for key, value in override.items():
                if value is None:
                    body.pop(key, None)
                else:
                    body[key] = value
    return body


def _generate_one(model: str, item: Item, prompt: str, api_key: str) -> dict:
    # `retryable` marks transport failures, which a resumed pass retries; a
    # model's own null or invalid answer is a result and is kept.
    record: dict = {"model": model, "item_id": item.id, "source_id": item.source_id,
                    "valid": False, "output": None, "cost": 0.0, "error": None, "retryable": True}
    start = time.monotonic()
    try:
        resp = httpx.post(
            _OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": _USER_AGENT},
            json=eval_body(model, prompt),
            timeout=180.0,
        )
        body = resp.json()
        if resp.status_code != 200:
            record["error"] = f"HTTP {resp.status_code}: {(body.get('error') or {}).get('message')}"
            return record
        record["retryable"] = False
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        record.update(
            finish_reason=choice.get("finish_reason"),
            provider=body.get("provider"),
            completion_tokens=usage.get("completion_tokens"),
            cost=float(usage.get("cost") or 0.0),
        )
        data = _extract_json(choice["message"].get("content"))
        if data is not None and _validate_response(data, item):
            if isinstance(data.get("deadline_date"), date):
                data["deadline_date"] = data["deadline_date"].isoformat()
            record.update(valid=True, output=data)
        else:
            record["error"] = "invalid or null response"
    except Exception as e:
        record["error"] = str(e)
        record["retryable"] = True
    finally:
        record["latency_s"] = round(time.monotonic() - start, 2)
    return record


def _generate(args, config: AppConfig, out: Path) -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    sample_path = out / "sample.jsonl"
    if sample_path.exists():
        sample = [Item.from_dict(d) for d in _read_jsonl(sample_path)]
        logger.info("reusing sample of %d items", len(sample))
    else:
        store = Store()
        now = datetime.now(timezone.utc)
        since = max(now - timedelta(days=args.days), config.max_age_cutoff(now))
        stored = store.classifications_as_of(now, config_hash=config.config_hash(), since=since)
        items = store.all_items_in_range(since, now)
        sample = select_sample(items, stored, args.sample, random.Random(args.seed))
        cache = HttpCache()
        sample = [_with_fr_body(it, cache) for it in sample]
        out.mkdir(parents=True, exist_ok=True)
        sample_path.write_text("".join(json.dumps(it.to_dict()) + "\n" for it in sample))
        logger.info("sampled %d items from %d in the last %d days", len(sample), len(items), args.days)

    gen_path = out / "generations.jsonl"
    done = {(r["model"], r["item_id"]) for r in _read_jsonl(gen_path) if not r["retryable"]}
    jobs = [(m, it) for m in models for it in sample if (m, it.id) not in done]
    logger.info("generate: %d calls to make (%d already done)", len(jobs), len(models) * len(sample) - len(jobs))

    prompts = {it.id: _build_prompt(it, config, config.suppress_threshold) for it in sample}
    lock = threading.Lock()
    spent = [0.0]

    def run(job: tuple[str, Item]) -> None:
        model, item = job
        with lock:
            if spent[0] >= args.max_usd:
                return
        rec = _generate_one(model, item, prompts[item.id], api_key)
        with lock:
            spent[0] += rec["cost"]
            with gen_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        if not rec["valid"]:
            logger.warning("%s on %s: %s", model, item.id, rec["error"])

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run, jobs))
    if spent[0] >= args.max_usd:
        logger.warning("stopped at the --max-usd cap ($%.4f); rerun to resume", spent[0])
    logger.info("generate: $%.4f spent this pass", spent[0])


# ── judge ───────────────────────────────────────────────────────────────────

def _latest_generations(out: Path) -> dict[tuple[str, str], dict]:
    """Last scorable record per (model, item); a resumed pass appends retries.
    Transport errors (a rejected parameter, a timeout) say nothing about the
    model's answers, so they are left out here and listed separately."""
    return {(r["model"], r["item_id"]): r for r in _read_jsonl(out / "generations.jsonl")
            if not r["retryable"]}


def _transport_errors(out: Path) -> dict[str, int]:
    latest = {(r["model"], r["item_id"]): r for r in _read_jsonl(out / "generations.jsonl")}
    counts: dict[str, int] = defaultdict(int)
    for (model, _), r in latest.items():
        if r["retryable"]:
            counts[model] += 1
    return dict(counts)


def blind(item_id: str, outputs: dict[str, dict], judge_pass: int) -> list[tuple[str, str, dict]]:
    """(label, model, output) in an order seeded by item and pass, so a rerun
    reproduces it and a second pass sees a different order."""
    models = sorted(outputs)
    random.Random(f"{item_id}:{judge_pass}").shuffle(models)
    return [(chr(ord("A") + i), m, outputs[m]) for i, m in enumerate(models)]


def _call_judge(prompt: str, model: str) -> dict:
    # Without ANTHROPIC_API_KEY the CLI uses the Claude Code subscription login;
    # with it, every judge call would bill the API key instead.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence",
           "--output-format", "json", "--json-schema", json.dumps(_JUDGE_SCHEMA),
           "--system-prompt", _JUDGE_SYSTEM]
    # A neutral cwd keeps this repo's CLAUDE.md out of the judge's context.
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=900, env=env, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    result = json.loads(proc.stdout)
    if result.get("is_error") or not isinstance(result.get("structured_output"), dict):
        raise RuntimeError(f"judge returned no structured output: {str(result.get('result'))[:300]}")
    return {"verdict": result["structured_output"], "judge_cost_usd": result.get("total_cost_usd")}


def _judge(args, config: AppConfig, out: Path) -> None:
    sample = {d["id"]: Item.from_dict(d) for d in _read_jsonl(out / "sample.jsonl")}
    gens = _latest_generations(out)
    judge_path = out / "judgments.jsonl"
    # Keyed with the candidate set: adding a model to an existing run re-judges
    # each item with it included, rather than leaving it unscored.
    done = {(r["item_id"], r["pass"], frozenset(r["labels"].values())) for r in _read_jsonl(judge_path)}

    jobs = []
    for item_id, item in sample.items():
        outputs = {m: r["output"] for (m, iid), r in gens.items() if iid == item_id and r["valid"]}
        if len(outputs) < 2:
            continue  # nothing to compare; invalid responses are scored in the report
        for p in range(args.passes):
            if (item_id, p, frozenset(outputs)) not in done:
                jobs.append((item, outputs, p))
    logger.info("judge: %d calls to make with %s, %d workers", len(jobs), args.judge_model, args.judge_workers)

    lock = threading.Lock()

    def run(job: tuple[Item, dict[str, dict], int]) -> None:
        item, outputs, p = job
        labelled = blind(item.id, outputs, p)
        prompt = _judge_prompt(_build_prompt(item, config, config.suppress_threshold),
                               [(label, o) for label, _, o in labelled])
        try:
            res = _call_judge(prompt, args.judge_model)
        except Exception as e:
            logger.warning("judge failed on %s pass %d: %s", item.id, p, e)
            return
        rec = {"item_id": item.id, "pass": p, "labels": {label: m for label, m, _ in labelled}, **res}
        with lock:
            with judge_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            logger.info("judged %s pass %d", item.id, p)

    with ThreadPoolExecutor(max_workers=args.judge_workers) as pool:
        list(pool.map(run, jobs))


# ── report ──────────────────────────────────────────────────────────────────

_CRITERIA = ("overall", "accuracy", "substance", "classification")


def aggregate(gens: dict[tuple[str, str], dict], judgments: list[dict]) -> dict[str, dict]:
    """Per-model metrics. An invalid response scores 1 on every criterion and
    ranks last for that item, so a model cannot win by failing on hard items."""
    models = sorted({m for m, _ in gens})
    scores: dict[str, dict[str, list[float]]] = {m: defaultdict(list) for m in models}
    ranks: dict[str, list[float]] = defaultdict(list)
    wins: dict[str, int] = defaultdict(int)
    by_source: dict[str, dict[str, list[float]]] = {m: defaultdict(list) for m in models}
    judged_items = set()

    for j in judgments:
        labels = j["labels"]
        verdict = j["verdict"]
        per_label = {c["label"]: c for c in verdict.get("candidates", []) if c.get("label") in labels}
        source = next((r["source_id"] for (m, iid), r in gens.items() if iid == j["item_id"]), "?")
        judged_items.add(j["item_id"])
        order = [lab for lab in verdict.get("ranking", []) if lab in labels]
        order += [lab for lab in labels if lab not in order]
        failed = [m for m in models
                  if (m, j["item_id"]) in gens and not gens[(m, j["item_id"])]["valid"]]
        field = len(order) + len(failed)
        for pos, lab in enumerate(order):
            m = labels[lab]
            ranks[m].append(pos + 1)
            if pos == 0:
                wins[m] += 1
            for c in _CRITERIA:
                if lab in per_label:
                    scores[m][c].append(float(per_label[lab][c]))
            if lab in per_label:
                by_source[m][source].append(float(per_label[lab]["overall"]))
        for m in failed:
            ranks[m].append(field)
            for c in _CRITERIA:
                scores[m][c].append(1.0)
            by_source[m][source].append(1.0)

    report = {}
    for m in models:
        recs = [r for (mm, _), r in gens.items() if mm == m]
        valid = [r for r in recs if r["valid"]]
        report[m] = {
            "n": len(recs),
            "valid_pct": 100.0 * len(valid) / len(recs) if recs else 0.0,
            **{c: statistics.fmean(scores[m][c]) if scores[m][c] else None for c in _CRITERIA},
            "mean_rank": statistics.fmean(ranks[m]) if ranks[m] else None,
            "win_pct": 100.0 * wins[m] / len(judgments) if judgments else 0.0,
            "usd_per_1k": 1000 * statistics.fmean(r["cost"] for r in recs) if recs else 0.0,
            "p50_latency_s": statistics.median(r["latency_s"] for r in recs) if recs else None,
            "by_source": {s: statistics.fmean(v) for s, v in sorted(by_source[m].items())},
        }
    return report


def _fmt(v: float | None, spec: str = ".2f") -> str:
    return "-" if v is None else format(v, spec)


def _report(args, out: Path) -> str:
    gens = _latest_generations(out)
    latest: dict[tuple[str, int], dict] = {}
    for j in _read_jsonl(out / "judgments.jsonl"):
        latest[(j["item_id"], j["pass"])] = j  # a re-judgment supersedes the earlier one
    judgments = list(latest.values())
    rep = aggregate(gens, judgments)
    order = sorted(rep, key=lambda m: -(rep[m]["overall"] or 0))
    judge_cost = sum(j.get("judge_cost_usd") or 0 for j in judgments)

    lines = [f"# Model comparison — {len({iid for _, iid in gens})} items, "
             f"{len(judgments)} judgments by {args.judge_model}", "",
             "| model | overall | accuracy | substance | classif. | mean rank | wins | valid | $/1k items | p50 s |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for m in order:
        r = rep[m]
        lines.append(f"| `{m}` | {_fmt(r['overall'])} | {_fmt(r['accuracy'])} | {_fmt(r['substance'])} "
                     f"| {_fmt(r['classification'])} | {_fmt(r['mean_rank'])} | {r['win_pct']:.0f}% "
                     f"| {r['valid_pct']:.0f}% | ${r['usd_per_1k']:.3f} | {_fmt(r['p50_latency_s'], '.1f')} |")
    sources = sorted({s for r in rep.values() for s in r["by_source"]})
    lines += ["", "## Overall score by source", "",
              "| model | " + " | ".join(sources) + " |", "|---|" + "---|" * len(sources)]
    for m in order:
        lines.append(f"| `{m}` | " + " | ".join(_fmt(rep[m]["by_source"].get(s)) for s in sources) + " |")
    errors = _transport_errors(out)
    if errors:
        lines += ["", "Transport errors, not scored (rerun to retry): "
                  + ", ".join(f"`{m}` {n}" for m, n in sorted(errors.items()))]
    overridden = [m for m in order if eval_body(m, "") != request_body(m, "", max_tokens=2048)]
    if overridden:
        lines += ["", "Ran with request overrides (see _MODEL_OVERRIDES; production would need the same): "
                  + ", ".join(f"`{m}`" for m in overridden)]
    lines += ["", f"Judge notional cost (subscription, list price): ${judge_cost:.2f}"]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text)
    (out / "report.json").write_text(json.dumps(rep, indent=2))
    return text


def cmd_compare_models(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_config(args.config)
    out = Path(args.out)
    if args.stage in ("generate", "all"):
        _generate(args, config, out)
    if args.stage in ("judge", "all"):
        _judge(args, config, out)
    if args.stage in ("report", "all"):
        print(_report(args, out))
    return 0
