from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from sweepreader.config import load_config
from sweepreader.ingest.base import fetch_source
from sweepreader.ingest.cluster import assign_clusters
from sweepreader.classify.classifier import OpenRouterClient, keyword_fallback
from sweepreader.store import Store, StateStore
from sweepreader.render import render_page

logger = logging.getLogger(__name__)


def _classify_item(item, existing_cls, llm, config, config_hash, dry_run, store):
    """Classify one item, skipping if already LLM-classified. Thread-safe."""
    if existing_cls is not None and not existing_cls.unclassified:
        return  # already have a real LLM classification
    if llm is None and existing_cls is not None:
        return  # no LLM and already have any classification — keep it

    cls = llm.classify(item, config) if llm is not None else keyword_fallback(item, config.model, config_hash)

    if not dry_run:
        store.append_classification(cls, force=(existing_cls is not None))


def _run_parallel(items, existing_clss, llm, config, config_hash, dry_run, store, label):
    total = len(items)
    if total == 0:
        return
    counter = threading.local()
    done_count = [0]
    done_lock = threading.Lock()

    logger.info("%s: %d items to classify", label, total)

    with ThreadPoolExecutor(max_workers=config.classify_concurrency) as pool:
        futures = {
            pool.submit(_classify_item, item, existing_clss.get(item.id),
                        llm, config, config_hash, dry_run, store): item
            for item in items
        }
        for future in as_completed(futures):
            future.result()  # re-raise any exception from the thread
            with done_lock:
                done_count[0] += 1
                n = done_count[0]
            if n % 10 == 0 or n == total:
                logger.info("%s: %d/%d classified, %d remaining", label, n, total, total - n)


def cmd_run(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

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

    assign_clusters(all_new_items)

    now = datetime.now(timezone.utc)
    existing_clss = store.classifications_as_of(now, config.model, config_hash)

    new_count = 0
    for item in all_new_items:
        if store.append_item(item):
            new_count += 1

    logger.info("total new_items=%d", new_count)

    # Classify all fetched items (new or needing upgrade from keyword fallback)
    to_classify_fetched = [
        item for item in all_new_items
        if not (existing_clss.get(item.id) is not None and not existing_clss[item.id].unclassified)
        and not (llm is None and existing_clss.get(item.id) is not None)
    ]
    _run_parallel(to_classify_fetched, existing_clss, llm, config, config_hash,
                  args.dry_run, store, "classify")

    # Backfill: items in the trailing window that need classification under the current
    # hash — either no classification exists yet, or they fell back to keyword and should
    # be upgraded now that an LLM is available. Skip items older than 6 months.
    six_months_ago = now - timedelta(days=183)
    fetched_ids = {item.id for item in all_new_items}
    backfill = [
        item for item in store.items_as_of(now, config.trailing_days)
        if item.id not in fetched_ids
        and item.published_at >= six_months_ago
        and (
            existing_clss.get(item.id) is None
            or existing_clss[item.id].unclassified
        )
    ]
    _run_parallel(backfill, existing_clss, llm, config, config_hash,
                  args.dry_run, store, "backfill")

    state.set("failures_this_run", failures)
    state.set("source_health", per_source_health)

    if not args.dry_run:
        state.save()
        render_page(config, store, state)

    if failures > 0:
        logger.warning("%d source(s) failed this run", failures)

    return 0
