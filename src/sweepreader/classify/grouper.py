"""LLM adjudication of candidate groups.

The heuristic in `sweepreader.grouping` produces candidates from exact blocking
keys; this module asks the model to confirm each one really is a single event and
to write the group's summary (GROUPING.md decision 2).

Only candidates are ever sent, never all pairs. Groups are keyed on their
membership, so an unchanged group is never re-adjudicated and the summary is
written once per distinct membership set.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from sweepreader.classify.classifier import _OPENROUTER_URL, _USER_AGENT, _extract_json
from sweepreader.store.models import Group

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_BATCH = 10
_MAX_MEMBERS_SHOWN = 8


def _build_prompt(batch: list[tuple[Group, list["Item"]]]) -> str:
    lines = [
        "You are de-duplicating exchange notices for an options market-making desk.",
        "",
        "The same underlying event reaches this feed more than once in two ways:",
        "",
        "  1. Per-market cross-post — one exchange group publishes the same notice",
        "     separately to each of its markets. MIAX posts one alert each to",
        "     Options, Pearl, Emerald and Sapphire; NYSE does the same across Arca,",
        "     American and National.",
        "",
        "  2. Same document, several URLs or feeds — one specification or notice is",
        "     served under multiple paths (e.g. `.../boev3-specification` and",
        "     `.../boev3-specification/overview`) and carried by several of the same",
        "     exchange's feeds (Cboe's options, equities and futures tech feeds all",
        "     list the same spec). Here the venues may be identical and only the",
        "     source feed or URL differs.",
        "",
        "Both are ONE event. But notices that merely look alike and concern different",
        "securities, dates, versions or actions are DIFFERENT events. In particular a",
        "spec for one market is not the same document as the equivalent spec for",
        "another (an Options BOEv3 spec is not the Equities BOEv3 spec), and two",
        "corporate-action notices naming different symbols are different events.",
        "",
        "For each group decide whether all members describe one underlying event.",
        "",
    ]
    for gi, (_group, members) in enumerate(batch):
        lines.append(f"GROUP {gi}:")
        for mi, m in enumerate(members[:_MAX_MEMBERS_SHOWN]):
            lines.append(
                f"  [{mi}] venue={m.venue} | feed={m.source_id} | {m.title}"
            )
            lines.append(f"       url={_short_url(m.url)}")
        if len(members) > _MAX_MEMBERS_SHOWN:
            lines.append(f"  ... and {len(members) - _MAX_MEMBERS_SHOWN} more")
        lines.append("")
    lines += [
        "Respond with JSON only:",
        '{"groups": [{"index": 0, "same_event": true, "confidence": 0.0-1.0,',
        '             "summary": "one sentence covering the whole group"}]}',
        "",
        "The summary should describe the event once, for a reader who will see a",
        "single card standing in for every member. Do not enumerate the markets —",
        "they are shown separately.",
        "",
        "Set same_event false if you are unsure. Showing two cards is much better",
        "than hiding a notice inside the wrong group.",
    ]
    return "\n".join(lines)


def _short_url(url: str, limit: int = 120) -> str:
    """URL path carries the signal for the multi-path case; the host rarely does."""
    return url if len(url) <= limit else url[:limit] + "..."


def _post(prompt: str, config: "AppConfig", api_key: str) -> dict | None:
    for attempt in range(3):
        try:
            resp = httpx.post(
                _OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": _USER_AGENT,
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 2048,
                },
                timeout=60.0,
            )
            if resp.status_code == 429:
                delay = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning("group adjudication rate limited; sleeping %.1fs", delay)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content")
            if content is None:
                continue
            return _extract_json(content)
        except Exception as e:
            logger.warning("group adjudication call failed (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def adjudicate(
    groups: list[Group],
    items_by_id: dict[str, "Item"],
    config: "AppConfig",
    *,
    api_key: str | None = None,
    now: datetime | None = None,
) -> list[Group]:
    """Confirm or reject each candidate group.

    Rejected groups are dropped entirely, so their members render as individual
    cards — the safe direction. A failed or unparseable model response leaves the
    heuristic decision untouched rather than discarding it.
    """
    now = now or datetime.now(timezone.utc)
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        logger.info("no OPENROUTER_API_KEY; keeping %d heuristic group(s)", len(groups))
        return groups

    out: list[Group] = []
    for start in range(0, len(groups), _BATCH):
        chunk = groups[start:start + _BATCH]
        batch = [(g, [items_by_id[m] for m in g.member_ids if m in items_by_id]) for g in chunk]
        batch = [(g, ms) for g, ms in batch if len(ms) >= 2]
        if not batch:
            continue

        data = _post(_build_prompt(batch), config, key)
        if not data or "groups" not in data:
            logger.warning("group adjudication returned nothing usable; keeping %d heuristic group(s)", len(batch))
            out.extend(g for g, _ in batch)
            continue

        verdicts = {int(v["index"]): v for v in data["groups"] if isinstance(v, dict) and "index" in v}
        for gi, (group, _members) in enumerate(batch):
            v = verdicts.get(gi)
            if v is None:
                out.append(group)          # no verdict: keep the heuristic decision
                continue
            if not v.get("same_event", False):
                logger.info("group %s rejected by model", group.group_id[:8])
                continue
            out.append(Group(
                group_id=group.group_id,
                member_ids=group.member_ids,
                canonical_id=group.canonical_id,
                decided_at=now,
                decided_by="llm",
                confidence=float(v.get("confidence", 1.0)),
                summary=(v.get("summary") or None),
                model=config.model,
            ))
    return out


def to_json(groups: list[Group]) -> str:
    """Debug helper for the backtest CLI."""
    return json.dumps([g.to_dict() for g in groups], indent=2)
