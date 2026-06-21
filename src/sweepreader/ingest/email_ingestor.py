from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sweepreader.ingest.base import BaseAdapter
from sweepreader.store.models import Item

if TYPE_CHECKING:
    from sweepreader.config import SourceConfig

logger = logging.getLogger(__name__)


class EmailIngestor(BaseAdapter):
    """Phase 2 — IMAP email ingestion (stub)."""

    def fetch(self) -> list[Item]:
        logger.info("EmailIngestor: Phase 2 not yet implemented for source=%s", self.source.id)
        return []
