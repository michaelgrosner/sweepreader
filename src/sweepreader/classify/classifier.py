from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import httpx

from sweepreader.deadlines import sanitize_deadline_kind
from sweepreader.store.models import Classification
from sweepreader.tags import TAG_AXES, sanitize_tags

if TYPE_CHECKING:
    from sweepreader.config import AppConfig
    from sweepreader.store.models import Item

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_USER_AGENT = "SweepReader/0.1"

# Statuses that mean the key itself is unusable for the rest of the run: the
# daily spend cap (403), no credit (402), a revoked key (401). Retrying them per
# item only repeats the refusal — one run logged 326 of them in its last minute.
_FATAL_STATUSES = frozenset({401, 402, 403})


def request_body(model: str, prompt: str, *, max_tokens: int) -> dict:
    """The chat-completions payload shared by classification and group adjudication."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        # DeepSeek v4 flash reasons before it answers, and the reasoning counts
        # against max_tokens. On title-only notices it deliberated to the cap and
        # returned finish_reason "length" with null content — billed in full, and
        # a retry of the same prompt hit the cap again. This is extraction against
        # a fixed rubric: with reasoning off it answers in ~100-250 tokens, and
        # tiers matched the stored reasoning-on classifications 22 times in 24.
        "reasoning": {"enabled": False},
        # Route only to providers that honour every parameter above; one that
        # silently ignored `reasoning` would bring the null responses back.
        "provider": {"require_parameters": True},
    }


def response_content(body: dict, what: str) -> tuple[str | None, str | None]:
    """(content, finish_reason). Logs the reason when content is missing — a bare
    "null content" told us nothing, and the cause sat in fields we threw away."""
    choice = (body.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    finish = choice.get("finish_reason")
    if content is None:
        usage = body.get("usage") or {}
        logger.warning(
            "%s: null content (finish_reason=%s, completion_tokens=%s, reasoning_tokens=%s, provider=%s)",
            what, finish, usage.get("completion_tokens"),
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            body.get("provider"),
        )
    return content, finish


def refusal_message(resp: httpx.Response) -> str:
    """OpenRouter's own one-line reason for an error status, without httpx's
    multi-line exception text."""
    try:
        return str(resp.json()["error"]["message"])
    except Exception:
        return resp.reason_phrase

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer", "minimum": 0, "maximum": 100},
        "tier": {"type": "string", "enum": ["A", "B", "C", "D", "E"]},
        "venues": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "deadline_date": {"type": ["string", "null"]},
        "deadline_kind": {"type": ["string", "null"]},
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
    # Ordering matters: all config-static content (role, tiers, tags, schema) comes
    # first so it forms a stable prefix the provider can cache across items in a run;
    # the per-item block must stay last. Don't move it back up.
    tier_desc = "\n".join(f"  {k}: {v}" for k, v in _TIER_DESCRIPTIONS.items())
    return f"""You are classifying a financial regulatory item for relevance to:
{config.profile_prompt.strip()}

Tier definitions:
{tier_desc}

Tag axes (pick ONLY applicable tags from these exact values; omit any that don't apply):
{_tag_guidance()}

Deadline fields:
- deadline_date: if the text states a date the reader must act before — certification window open/close, cutover, mandatory upgrade-by, comment-period close, or retirement of a feed/port/protocol — return it as YYYY-MM-DD. Otherwise return null. Do not infer, extrapolate, or convert a relative phrase ("in 30 days") into a date; return null unless an explicit calendar date is present in the text. The item's own publication date is never a deadline.
- deadline_kind: if deadline_date is present, one of: cert-window-opens, cert-window-closes, cutover, upgrade-by, comment-closes, retirement. Otherwise return null.
- A date the reader merely observes is not a deadline. Return null for the day a security starts or stops trading, IPO/listing/transfer/symbol-change effective dates, halts and resumptions, series list/delist, expirations, index rebalances, dividend and other corporate-action dates, meeting or holiday dates, and effective dates of fee schedules. Only a date the reader's own systems must be ready by counts.
- The presence of a date does not raise an item's tier or relevance. Tier is judged on substance alone: a corporate action with a hard date is still tier E.

Respond ONLY with valid JSON matching this schema:
{{
  "relevance": <integer 0-100>,
  "tier": <"A"|"B"|"C"|"D"|"E">,
  "venues": [<exchange codes affected>],
  "tags": [<zero or more tags from the axes above, exact values only>],
  "deadline_date": <"YYYY-MM-DD" or null>,
  "deadline_kind": <"cert-window-opens"|"cert-window-closes"|"cutover"|"upgrade-by"|"comment-closes"|"retirement" or null>,
  "rationale": <1-2 sentence rationale>,
  "summary": <2-3 sentence summary for the reader, or null if relevance < {suppress_threshold}>
}}
No other text — just the JSON object.

Write all output in English.

Summary style: lead with the substance — which rule, field, message type, order
type, symbol set or session changes, and the dates that matter. Omit generic
closers ("engineers should update their systems", "warrants review"); the reader
knows why they subscribed. Never state what the text does not contain ("no
details are provided", "the notice does not specify dates") — the text you see
may be an excerpt, so absence in it says nothing about the filing. If you cannot
say anything beyond what the title already says, return null for summary rather
than restating the title.

Classify the item below:
Title: {item.title}
Source: {item.source_id}
Venue: {item.venue}
Published: {item.published_at.strftime("%Y-%m-%d")}
Text:
{item.raw_text[:3000]}"""


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" for c in s)


def _validate_response(data: dict, item: "Item | None" = None) -> bool:
    # The model occasionally answers in Chinese despite the instruction (~2% of
    # calls observed). Treat it as an invalid response so the retry loop runs
    # rather than storing an unreadable summary.
    for field in ("summary", "rationale"):
        val = data.get(field)
        if isinstance(val, str) and _has_cjk(val):
            return False
    if not isinstance(data.get("relevance"), int):
        return False
    if data.get("tier") not in ("A", "B", "C", "D", "E"):
        return False
    if not isinstance(data.get("rationale"), str):
        return False
    if not isinstance(data.get("venues"), list):
        return False

    # Deadline validation
    data["deadline_kind"] = sanitize_deadline_kind(data.get("deadline_kind"))

    raw_date = data.get("deadline_date")
    parsed_date: date | None = None
    if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
        parsed_date = raw_date
    elif isinstance(raw_date, datetime):
        parsed_date = raw_date.date()
    elif isinstance(raw_date, str):
        try:
            parsed_date = date.fromisoformat(raw_date.strip())
        except (ValueError, TypeError):
            parsed_date = None

    if parsed_date is not None and item is not None and item.published_at is not None:
        pub = item.published_at.date() if isinstance(item.published_at, datetime) else item.published_at
        try:
            min_date = pub.replace(year=pub.year - 2)
        except ValueError:
            min_date = pub.replace(year=pub.year - 2, day=28)
        try:
            max_date = pub.replace(year=pub.year + 5)
        except ValueError:
            max_date = pub.replace(year=pub.year + 5, day=28)
        if parsed_date < min_date or parsed_date > max_date:
            parsed_date = None

    data["deadline_date"] = parsed_date
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
        self._lock = threading.Lock()
        # Set once the key is refused (see _FATAL_STATUSES); every later item goes
        # straight to the keyword fallback and is backfilled on a later run.
        self.halted: str | None = None
        self.classified = 0
        self.fell_back = 0
        self.cost = 0.0

    def stats(self) -> dict:
        return {"classified": self.classified, "fell_back": self.fell_back,
                "cost_usd": round(self.cost, 6), "halted": self.halted}

    def _halt(self, resp: httpx.Response) -> None:
        with self._lock:
            if self.halted is not None:
                return
            self.halted = f"HTTP {resp.status_code}: {refusal_message(resp)}"
        logger.error("OpenRouter refused the key (%s); keyword fallback for the rest of this run",
                     self.halted)

    def _fallback(self, item: "Item", config: "AppConfig", config_hash: str) -> Classification:
        with self._lock:
            self.fell_back += 1
        return keyword_fallback(item, config.model, config_hash)

    def classify(self, item: "Item", config: "AppConfig") -> Classification:
        config_hash = config.config_hash()
        if self.halted is not None:
            return self._fallback(item, config, config_hash)
        prompt = _build_prompt(item, config, config.suppress_threshold)

        for attempt in range(3):
            try:
                resp = httpx.post(
                    _OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "User-Agent": _USER_AGENT,
                        "Content-Type": "application/json",
                    },
                    # Summary guidance produces longer output; 1024 truncated
                    # the JSON mid-object and lost items to the fallback.
                    json=request_body(config.model, prompt, max_tokens=2048),
                    timeout=60.0,
                )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                    logger.warning("Rate limited; sleeping %.1fs (attempt %d)", retry_after, attempt + 1)
                    time.sleep(retry_after)
                    continue
                if resp.status_code in _FATAL_STATUSES:
                    self._halt(resp)
                    return self._fallback(item, config, config_hash)
                resp.raise_for_status()
                body = resp.json()
                with self._lock:
                    self.cost += float((body.get("usage") or {}).get("cost") or 0.0)
                content, finish = response_content(body, f"item {item.id} attempt {attempt + 1}")
                if content is None:
                    if finish == "length":
                        break  # the same prompt truncates the same way; don't pay for it twice more
                    continue
                data = _extract_json(content)
                if data and _validate_response(data, item):
                    with self._lock:
                        self.classified += 1
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
                        deadline_date=data.get("deadline_date"),
                        deadline_kind=data.get("deadline_kind"),
                    )
                logger.warning("LLM returned invalid JSON on attempt %d for item %s: %.120r",
                               attempt + 1, item.id, content)
            except httpx.HTTPStatusError as e:
                logger.warning("LLM call failed on attempt %d for item %s: HTTP %d: %s",
                               attempt + 1, item.id, e.response.status_code, refusal_message(e.response))
                if attempt < 2:
                    time.sleep(2 ** attempt)
            except Exception as e:
                logger.warning("LLM call failed on attempt %d for item %s: %s", attempt + 1, item.id, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)

        logger.error("LLM classification failed after retries for item %s, using keyword fallback", item.id)
        return self._fallback(item, config, config_hash)
