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

    from_dt = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
    to_dt = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)

    items = store.all_items_in_range(from_dt, to_dt)
    logger.info("Backtest: %d items in range [%s, %s]", len(items), args.from_date, args.to_date)

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
    classifications = store.classifications_as_of(to_dt, config.model, config_hash)
    visible, suppressed = rank_items(items, classifications, config, to_dt)

    print(f"\n=== Backtest result: {args.from_date} → {args.to_date} ===")
    print(f"  Config: {args.config}  (hash={config_hash})")
    print(f"  Items: {len(items)} total, {len(visible)} visible, {len(suppressed)} suppressed")
    print()
    for item, cls, score in visible[:20]:
        print(f"  [{cls.tier}] {score:5.1f}  {item.venue:8s}  {item.title[:70]}")

    return 0
