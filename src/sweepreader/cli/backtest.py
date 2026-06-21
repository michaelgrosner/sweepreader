from __future__ import annotations

import logging
from datetime import datetime, timezone

from sweepreader.config import load_config
from sweepreader.classify.classifier import OpenRouterClient, keyword_fallback
from sweepreader.score import rank_items
from sweepreader.store import Store

logger = logging.getLogger(__name__)


def cmd_backtest(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    store = Store()

    dt_from = datetime.fromisoformat(args.from_date)
    from_dt = dt_from.astimezone(timezone.utc) if dt_from.tzinfo else dt_from.replace(tzinfo=timezone.utc)

    dt_to = datetime.fromisoformat(args.to_date)
    to_dt = dt_to.astimezone(timezone.utc) if dt_to.tzinfo else dt_to.replace(tzinfo=timezone.utc)

    # Hard floor (SPEC: max_age_days): never classify/score past the max-age cutoff.
    floor = config.max_age_cutoff(to_dt)
    if from_dt < floor:
        logger.info("Backtest: from %s clamped to max_age_days=%d (%s)",
                    args.from_date, config.max_age_days, floor.date())
        from_dt = floor

    items = store.all_items_in_range(from_dt, to_dt)
    logger.info("Backtest: %d items in range [%s, %s]", len(items), from_dt.date(), to_dt.date())

    config_hash = config.config_hash()

    try:
        llm = OpenRouterClient()
    except ValueError:
        llm = None
        logger.warning("No OPENROUTER_API_KEY — using keyword fallback for uncached items")

    new_cls = 0
    cached = 0
    for item in items:
        if store.has_classification(item.id, config.model, config_hash):
            cached += 1
            continue
        if llm is not None:
            cls = llm.classify(item, config)
        else:
            cls = keyword_fallback(item, config.model, config_hash)
        store.append_classification(cls)
        new_cls += 1

    logger.info("Backtest: %d cached, %d newly classified", cached, new_cls)

    # Emit ranked results as-of the to_dt
    classifications = store.classifications_as_of(to_dt, config.model, config_hash, since=from_dt)
    visible, suppressed = rank_items(items, classifications, config, to_dt)

    print(f"\n=== Backtest result: {args.from_date} → {args.to_date} ===")
    print(f"  Config: {args.config}  (hash={config_hash})")
    print(f"  Items: {len(items)} total, {len(visible)} visible, {len(suppressed)} suppressed")
    print()
    for item, cls, score in visible[:20]:
        print(f"  [{cls.tier}] {score:5.1f}  {item.venue:8s}  {item.title[:70]}")

    return 0
