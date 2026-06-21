from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from sweepreader.store.models import Classification
from sweepreader.tags import TAG_AXES, sanitize_tags

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_USER_AGENT = "SweepReader/0.1"

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer", "minimum": 0, "maximum": 100},
        "tier": {"type": "string", "enum": ["A", "B", "C", "D", "E"]},
        "venues": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "summary": {"type": ["string", "null"]},
    },
    "required": ["relevance", "tier", "venues", "rationale"],
}

_TIER_DESCRIPTIONS = {
    "A": "Technical/upcoming features: new venues, order types, protocol/spec changes, certification windows, feed/connectivity/colo/symbology/migrations",
    "B": "Market-structure news: SEC policy, competitive developments, structurally notable enforcement",
    "C": "Exchange operational: fee changes, membership/access, system status/incidents, hours/holidays, routine disciplinary",
    "D": "Structural rule filings (MM-affecting): quoting obligations, 15c3-5, tick size, complex orders, PFOF/606, Reg SHO, OCC margin",
    "E": "Noise: corporate actions, trading halts, series list/delist, M&A",
}


def _tag_guidance() -> str:
    return "\n".join(f"  {axis}: {', '.join(tags)}" for axis, tags in TAG_AXES.items())


def _build_prompt(item: "Item", config: "AppConfig", suppress_threshold: int) -> str:
    tier_desc = "\n".join(f"  {k}: {v}" for k, v in _TIER_DESCRIPTIONS.items())
    return f"""You are classifying a financial regulatory item for relevance to:
{config.profile_prompt.strip()}

Tier definitions:
{tier_desc}

Tag axes (pick ONLY applicable tags from these exact values; omit any that don't apply):
{_tag_guidance()}

Item to classify:
Title: {item.title}
Source: {item.source_id}
Venue: {item.venue}
Published: {item.published_at.strftime("%Y-%m-%d")}
Text:
{item.raw_text[:3000]}

Respond ONLY with valid JSON matching this schema:
{{
  "relevance": <integer 0-100>,
  "tier": <"A"|"B"|"C"|"D"|"E">,
  "venues": [<exchange codes affected>],
  "tags": [<zero or more tags from the axes above, exact values only>],
  "rationale": <1-2 sentence rationale>,
  "summary": <2-3 sentence summary for the reader, or null if relevance < {suppress_threshold}>
}}

No other text — just the JSON object."""


def _validate_response(data: dict) -> bool:
    if not isinstance(data.get("relevance"), int):
        return False
    if data.get("tier") not in ("A", "B", "C", "D", "E"):
        return False
    if not isinstance(data.get("rationale"), str):
        return False
    if not isinstance(data.get("venues"), list):
        return False
    return True


def _extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown fences or surrounding prose and try again
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


_KEYWORD_TIERS: list[tuple[list[str], str, int]] = [
    (["specification", "spec update", "protocol", "certification", "connectivity", "colo", "symbology", "migration", "new order type", "new venue"], "A", 65),
    (["market structure", "sec proposal", "policy", "enforcement"], "B", 50),
    (["fee change", "membership", "system status", "hours", "holiday", "disciplinary"], "C", 35),
    (["rule change", "15c3-5", "tick size", "quoting obligation", "pfof", "reg sho", "margin"], "D", 40),
    (["halt", "corporate action", "delist", "m&a", "acquisition", "merger"], "E", 10),
]


def _fallback_tags(item: "Item") -> list[str]:
    """Conservative tags derivable without the LLM: market from the source/venue,
    plus rule-filing for Federal Register items."""
    sid = item.source_id.lower()
    blob = (sid + " " + item.venue.lower())
    tags: list[str] = []
    if "option" in blob:
        tags.append("options")
    if "equit" in blob:
        tags.append("equities")
    if "futur" in blob:
        tags.append("futures")
    if sid == "fed_register_sro":
        tags.append("rule-filing")
    return tags


def keyword_fallback(item: "Item", model: str, config_hash: str) -> Classification:
    title_lower = item.title.lower()
    text_lower = item.raw_text.lower()
    combined = title_lower + " " + text_lower

    tier, relevance = "C", 30
    for keywords, t, rel in _KEYWORD_TIERS:
        if any(kw in combined for kw in keywords):
            tier = t
            relevance = rel
            break

    return Classification(
        item_id=item.id,
        model=model,
        config_hash=config_hash,
        classified_at=datetime.now(timezone.utc),
        relevance=relevance,
        tier=tier,
        rationale="Keyword-based fallback classification",
        summary=None,
        venues=[item.venue],
        tags=sanitize_tags(_fallback_tags(item)),
        unclassified=True,
    )


class LlmClient(ABC):
    @abstractmethod
    def classify(self, item: "Item", config: "AppConfig") -> Classification:
        ...


class OpenRouterClient(LlmClient):
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self._api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

    def classify(self, item: "Item", config: "AppConfig") -> Classification:
        prompt = _build_prompt(item, config, config.suppress_threshold)
        config_hash = config.config_hash()

        for attempt in range(3):
            try:
                resp = httpx.post(
                    _OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "User-Agent": _USER_AGENT,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 1024,
                    },
                    timeout=60.0,
                )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                    logger.warning("Rate limited; sleeping %.1fs (attempt %d)", retry_after, attempt + 1)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"].get("content")
                if content is None:
                    logger.warning("LLM returned null content on attempt %d for item %s", attempt + 1, item.id)
                    continue
                data = _extract_json(content)
                if data and _validate_response(data):
                    return Classification(
                        item_id=item.id,
                        model=config.model,
                        config_hash=config_hash,
                        classified_at=datetime.now(timezone.utc),
                        relevance=int(data["relevance"]),
                        tier=data["tier"],
                        rationale=data["rationale"],
                        summary=data.get("summary"),
                        venues=data.get("venues", [item.venue]),
                        tags=sanitize_tags(data.get("tags")),
                        unclassified=False,
                    )
                logger.warning("LLM returned invalid JSON on attempt %d for item %s: %.120r",
                               attempt + 1, item.id, content)
            except Exception as e:
                logger.warning("LLM call failed on attempt %d for item %s: %s", attempt + 1, item.id, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)

        logger.error("LLM classification failed after retries for item %s, using keyword fallback", item.id)
        return keyword_fallback(item, config.model, config_hash)
