from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sweepreader.config import load_config
from sweepreader.ingest.base import fetch_source
from sweepreader.ingest.cluster import assign_clusters
from sweepreader.classify.classifier import OpenRouterClient, keyword_fallback
from sweepreader.store import Store, StateStore
from sweepreader.render import render_page

logger = logging.getLogger(__name__)


def cmd_run(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    store = Store()
    state = StateStore()

    config_hash = config.config_hash()
    state.set("config_hash", config_hash)

    try:
        llm = OpenRouterClient()
    except ValueError:
        logger.warning("OPENROUTER_API_KEY not set — using keyword fallback for all items")
        llm = None

    failures = 0
    per_source_health: dict = state.get("source_health", {})

    all_new_items = []
    for source in config.sources:
        if not source.enabled:
            continue

        items, err = fetch_source(source, state)
        if err:
            failures += 1
            per_source_health[source.id] = {"status": "error", "error": str(err)}
            continue

        per_source_health[source.id] = {"status": "ok", "item_count": len(items)}
        all_new_items.extend(items)
        logger.info("source=%s fetched %d items", source.id, len(items))

    # Cluster across all sources before persisting
    assign_clusters(all_new_items)

    # Pre-load existing classifications so we can check unclassified status
    now = datetime.now(timezone.utc)
    existing_clss = store.classifications_as_of(now, config.model, config_hash)

    new_count = 0
    for item in all_new_items:
        added = store.append_item(item)
        if not added:
            # Item already in store — check if it has a keyword-fallback classification
            # that should be upgraded now that an LLM is available.
            existing = existing_clss.get(item.id)
            if existing is None or not existing.unclassified or llm is None:
                continue
        else:
            new_count += 1
            existing = existing_clss.get(item.id)

        # Skip if already LLM-classified
        if existing is not None and not existing.unclassified:
            continue

        if llm is not None:
            cls = llm.classify(item, config)
        else:
            cls = keyword_fallback(item, config.model, config_hash)

        if not args.dry_run:
            # force=True upgrades a keyword-fallback record with the LLM result
            store.append_classification(cls, force=(existing is not None and existing.unclassified))

    logger.info("total new_items=%d", new_count)

    state.set("failures_this_run", failures)
    state.set("source_health", per_source_health)

    if not args.dry_run:
        state.save()
        render_page(config, store, state)

    if failures > 0:
        logger.warning("%d source(s) failed this run", failures)

    return 0
