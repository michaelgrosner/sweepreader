from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


Modality = Literal["api", "rss", "email"]
ParseStrategy = Literal["federal_register", "rss_generic", "email_html_or_pdf"]
TierLabel = Literal["A", "B", "C", "D", "E"]

_VALID_TIERS: set[str] = {"A", "B", "C", "D", "E"}
_VALID_MODALITIES: set[str] = {"api", "rss", "email"}
_VALID_PARSE: set[str] = {"federal_register", "rss_generic", "email_html_or_pdf"}


@dataclass
class SourceConfig:
    id: str
    modality: Modality
    parse: ParseStrategy
    default_tier_hint: TierLabel
    weight: float
    enabled: bool = True
    endpoint: str = ""
    address: str = ""


@dataclass
class AppConfig:
    model: str
    suppress_threshold: int
    trailing_days: int
    profile_prompt: str
    tier_weights: dict[TierLabel, float]
    sources: list[SourceConfig]

    def config_hash(self) -> str:
        blob = json.dumps(
            {
                "model": self.model,
                "suppress_threshold": self.suppress_threshold,
                "profile_prompt": self.profile_prompt,
                "tier_weights": self.tier_weights,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    _validate(raw, path)

    tier_weights: dict[TierLabel, float] = {}
    for tier, w in raw["tier_weights"].items():
        tier_weights[tier] = float(w)

    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()
    for s in raw["sources"]:
        sid = s["id"]
        if sid in seen_ids:
            raise ValueError(f"Duplicate source id: {sid!r}")
        seen_ids.add(sid)
        sources.append(
            SourceConfig(
                id=sid,
                modality=s["modality"],
                parse=s["parse"],
                default_tier_hint=s["default_tier_hint"],
                weight=float(s.get("weight", 1.0)),
                enabled=bool(s.get("enabled", True)),
                endpoint=s.get("endpoint", ""),
                address=s.get("address", ""),
            )
        )

    return AppConfig(
        model=raw["model"],
        suppress_threshold=int(raw["suppress_threshold"]),
        trailing_days=int(raw["trailing_days"]),
        profile_prompt=raw["profile_prompt"],
        tier_weights=tier_weights,
        sources=sources,
    )


def _validate(raw: dict, path: str | Path) -> None:
    required_top = ["model", "suppress_threshold", "trailing_days", "profile_prompt", "tier_weights", "sources"]
    for key in required_top:
        if key not in raw:
            raise ValueError(f"Config {path}: missing required key {key!r}")

    threshold = raw["suppress_threshold"]
    if not (0 <= threshold <= 100):
        raise ValueError(f"Config {path}: suppress_threshold must be 0-100, got {threshold}")

    weights = raw["tier_weights"]
    missing = _VALID_TIERS - set(weights.keys())
    if missing:
        raise ValueError(f"Config {path}: tier_weights missing tiers: {sorted(missing)}")

    for s in raw["sources"]:
        for req in ("id", "modality", "parse", "default_tier_hint"):
            if req not in s:
                raise ValueError(f"Config {path}: source {s.get('id', '?')} missing field {req!r}")
        if s["modality"] not in _VALID_MODALITIES:
            raise ValueError(f"Config {path}: source {s['id']} invalid modality {s['modality']!r}")
        if s["parse"] not in _VALID_PARSE:
            raise ValueError(f"Config {path}: source {s['id']} invalid parse {s['parse']!r}")
        if s["default_tier_hint"] not in _VALID_TIERS:
            raise ValueError(f"Config {path}: source {s['id']} invalid tier {s['default_tier_hint']!r}")
