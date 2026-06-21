from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class FeedbackStore:
    def __init__(self, data_dir: str | Path = "data"):
        self._dir = Path(data_dir) / "feedback"
        self._dir.mkdir(parents=True, exist_ok=True)

    def record(self, item_id: str, signal: str, config_hash: str) -> None:
        if signal not in ("up", "down"):
            raise ValueError(f"signal must be 'up' or 'down', got {signal!r}")
        entry = {
            "item_id": item_id,
            "signal": signal,
            "config_hash": config_hash,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        path = self._dir / f"{month}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def all_feedback(self) -> list[dict]:
        results: list[dict] = []
        for p in sorted(self._dir.glob("*.jsonl")):
            for line in p.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except Exception as e:
                        logger.warning("Corrupt feedback line in %s: %s", p, e)
        return results
