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

from sweepreader.config import load_config
from sweepreader.ingest import nyse, miax
from sweepreader.store import Store
from sweepreader.store.models import Item

logger = logging.getLogger(__name__)


def _seed_source(source, stop_before: datetime) -> Iterator[Item]:
    if source.parse == "nyse_notifications":
        yield from nyse.iter_seed_items(source.id, stop_before=stop_before)
    elif source.parse == "miax_alerts":
        yield from miax.iter_seed_items(source.id, source.endpoint, stop_before=stop_before)
    else:
        logger.info("seed: skipping %s (parse=%s not seedable)", source.id, source.parse)


def cmd_seed(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    store = Store()
    stop_before = datetime.now(timezone.utc) - timedelta(days=round(args.months * 30.44))

    if args.source:
        wanted = set(args.source.split(","))
        sources = [s for s in config.sources if s.id in wanted or s.parse in wanted]
    else:
        sources = [s for s in config.sources if s.parse in ("nyse_notifications", "miax_alerts")]

    logger.info("seed: %d source(s), back to %s", len(sources), stop_before.date())
    grand_new = 0
    for source in sources:
        new = seen = 0
        try:
            for item in _seed_source(source, stop_before):
                seen += 1
                if store.append_item(item):
                    new += 1
                if seen % 50 == 0:
                    logger.info("  %s: %d seen, %d new", source.id, seen, new)
        except Exception as e:  # per-source isolation
            logger.error("seed: source %s failed after %d items: %s", source.id, seen, e)
        logger.info("seed: %s done — %d seen, %d new", source.id, seen, new)
        grand_new += new

    logger.info("seed: complete — %d new items written", grand_new)
    return 0
