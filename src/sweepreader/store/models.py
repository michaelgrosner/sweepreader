from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Item:
    id: str
    source_id: str
    venue: str
    title: str
    url: str
    published_at: datetime
    first_seen_at: datetime
    raw_text: str
    modality: str
    cluster_id: Optional[str] = None

    @staticmethod
    def make_id(source_id: str, url: str) -> str:
        blob = f"{source_id}::{url}"
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "venue": self.venue,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "first_seen_at": self.first_seen_at.isoformat(),
            "raw_text": self.raw_text,
            "modality": self.modality,
            "cluster_id": self.cluster_id,
        }

    @staticmethod
    def from_dict(d: dict) -> "Item":
        return Item(
            id=d["id"],
            source_id=d["source_id"],
            venue=d["venue"],
            title=d["title"],
            url=d["url"],
            published_at=datetime.fromisoformat(d["published_at"]),
            first_seen_at=datetime.fromisoformat(d["first_seen_at"]),
            raw_text=d["raw_text"],
            modality=d["modality"],
            cluster_id=d.get("cluster_id"),
        )


@dataclass
class Classification:
    item_id: str
    model: str
    config_hash: str
    classified_at: datetime
    relevance: int
    tier: str
    rationale: str
    summary: Optional[str]
    venues: list[str] = field(default_factory=list)
    unclassified: bool = False

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "model": self.model,
            "config_hash": self.config_hash,
            "classified_at": self.classified_at.isoformat(),
            "relevance": self.relevance,
            "tier": self.tier,
            "rationale": self.rationale,
            "summary": self.summary,
            "venues": self.venues,
            "unclassified": self.unclassified,
        }

    @staticmethod
    def from_dict(d: dict) -> "Classification":
        return Classification(
            item_id=d["item_id"],
            model=d["model"],
            config_hash=d["config_hash"],
            classified_at=datetime.fromisoformat(d["classified_at"]),
            relevance=d["relevance"],
            tier=d["tier"],
            rationale=d["rationale"],
            summary=d.get("summary"),
            venues=d.get("venues", []),
            unclassified=d.get("unclassified", False),
        )
