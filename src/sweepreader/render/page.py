from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sweepreader.score import rank_items

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store import Store, StateStore

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"
_DOCS_DIR = Path("docs")

_TIER_COLORS = {
    "A": "#4F46E5",
    "B": "#3B82F6",
    "C": "#14B8A6",
    "D": "#F59E0B",
    "E": "#9CA3AF",
}


def _is_today(dt: datetime, now: datetime) -> bool:
    n = now.replace(tzinfo=None) if now.tzinfo else now
    d = dt.replace(tzinfo=None) if dt.tzinfo else dt
    today_start = n.replace(hour=0, minute=0, second=0, microsecond=0)
    return d >= today_start


def render_page(config: "AppConfig", store: "Store", state: "StateStore") -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.trailing_days)

    items = store.items_as_of(now, config.trailing_days)
    classifications = store.classifications_as_of(now, config.model, config.config_hash())

    visible, suppressed = rank_items(items, classifications, config, now)

    new_today = [(item, cls, score) for item, cls, score in visible if _is_today(item.published_at, now)]
    earlier = [(item, cls, score) for item, cls, score in visible if not _is_today(item.published_at, now)]

    source_health = state.get("source_health", {})
    failures = state.get("failures_this_run", 0)

    enabled_sources = [s for s in config.sources if s.enabled]
    coverage_codes = sorted({s.id.replace("_tech", "").replace("_reg", "").upper()[:8] for s in enabled_sources})

    recent_shards = _collect_shards(store, now)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["tier_color"] = lambda t: _TIER_COLORS.get(t, "#9CA3AF")
    env.filters["score_pct"] = lambda s: min(100, int(s))

    template = env.get_template("page.html")
    html = template.render(
        now=now,
        new_today=new_today,
        earlier=earlier,
        suppressed=suppressed,
        coverage_codes=coverage_codes,
        source_health=source_health,
        failures=failures,
        model=config.model,
        trailing_days=config.trailing_days,
        shards_json=json.dumps(recent_shards),
        tier_colors=_TIER_COLORS,
        tier_weights=config.tier_weights,
    )

    _DOCS_DIR.mkdir(exist_ok=True)
    (_DOCS_DIR / "index.html").write_text(html)
    logger.info("Page rendered: %d visible, %d suppressed", len(visible), len(suppressed))


def _collect_shards(store: "Store", now: datetime) -> list[dict]:
    """Collect last 2 months of items as lightweight JSON for client-side time-travel."""
    results: list[dict] = []
    for p in sorted(store._items_dir.glob("*.jsonl"), reverse=True)[:2]:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                import json as _json
                d = _json.loads(line)
                results.append({
                    "id": d["id"],
                    "title": d["title"],
                    "url": d["url"],
                    "venue": d["venue"],
                    "published_at": d["published_at"],
                    "first_seen_at": d["first_seen_at"],
                    "source_id": d["source_id"],
                })
            except Exception:
                pass
    return results
