"""Historical backfill for backtesting (SPEC §5).

Pages each scrapable source's full history back to a cutoff and appends the
items to the append-only store with ``first_seen_at = published_at`` so the
seeded past reconstructs faithfully under time-travel and backtest. Classification
is intentionally left to ``run``/``backtest`` (only uncached combos cost tokens).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Iterator

from sweepreader.classify.classifier import keyword_fallback
from sweepreader.config import load_config
from sweepreader.ingest import nyse, miax, iex, federal_register, opra
from sweepreader.ingest.http_cache import HttpCache
from sweepreader.store import Store
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)


def _make_body_gate(config, min_relevance: int):
    """Cheap, token-free relevance gate (keyword fallback) deciding which MIAX
    alerts warrant a full detail-page fetch. Skips obvious noise (tier E)."""
    model, config_hash = config.model, config.config_hash()

    def gate(item: Item) -> bool:
        cls = keyword_fallback(item, model, config_hash)
        return cls.tier != "E" and cls.relevance >= min_relevance

    return gate


def _seed_source(source, stop_before: datetime, *, cache, body_gate) -> Iterator[Item]:
    if source.parse == "federal_register":
        yield from federal_register.iter_seed_items(source.id, stop_before=stop_before, cache=cache)
    elif source.parse == "nyse_notifications":
        yield from nyse.iter_seed_items(source.id, stop_before=stop_before, cache=cache)
    elif source.parse == "iex_alerts":
        yield from iex.iter_seed_items(source.id, stop_before=stop_before, cache=cache)
    elif source.parse == "opra_notices":
        yield from opra.iter_seed_items(source.id, source.endpoint, stop_before=stop_before, cache=cache)
    elif source.parse == "miax_alerts":
        yield from miax.iter_seed_items(source.id, source.endpoint, stop_before=stop_before,
                                        body_gate=body_gate, cache=cache)
    else:
        logger.info("seed: skipping %s (parse=%s not seedable)", source.id, source.parse)


def cmd_seed(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    store = Store()
    now = datetime.now(timezone.utc)
    requested = now - timedelta(days=round(args.months * 30.44))
    # Hard floor (SPEC: max_age_days): never seed past the max-age cutoff, even if
    # --months asks for more.
    floor = config.max_age_cutoff(now)
    stop_before = max(requested, floor)
    if stop_before != requested:
        logger.info("seed: --months %.1f clamped to max_age_days=%d (%s)",
                    args.months, config.max_age_days, stop_before.date())
    cache = None if args.no_cache else HttpCache()
    body_gate = None if args.all_bodies else _make_body_gate(config, args.body_min_relevance)

    if args.source:
        wanted = set(args.source.split(","))
        sources = [s for s in config.sources if s.id in wanted or s.parse in wanted]
    else:
        sources = [s for s in config.sources if s.parse in (
            "federal_register", "nyse_notifications", "miax_alerts", "iex_alerts", "opra_notices")]

    logger.info("seed: %d source(s), back to %s", len(sources), stop_before.date())
    grand_new = 0
    for source in sources:
        new = seen = 0
        try:
            for item in _seed_source(source, stop_before, cache=cache, body_gate=body_gate):
                seen += 1
                if store.append_item(item):
                    new += 1
                if seen % 50 == 0:
                    logger.info("  %s: %d seen, %d new", source.id, seen, new)
        except Exception as e:  # per-source isolation
            logger.error("seed: source %s failed after %d items: %s", source.id, seen, e)
        logger.info("seed: %s done — %d seen, %d new", source.id, seen, new)
        grand_new += new

    if cache is not None:
        logger.info("seed: cache %d hits / %d misses", cache.hits, cache.misses)
    logger.info("seed: complete — %d new items written", grand_new)
    return 0
