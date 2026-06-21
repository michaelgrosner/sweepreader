from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sweepreader.config import SourceConfig
    from sweepreader.store.models import Item
    from sweepreader.store.store import StateStore

logger = logging.getLogger(__name__)

_USER_AGENT = "SweepReader/0.1 (contact: github.com/sweepreader)"


class BaseAdapter(ABC):
    def __init__(self, source: "SourceConfig", state: "StateStore | None" = None):
        self.source = source
        self.state = state

    @abstractmethod
    def fetch(self) -> list["Item"]:
        ...


def fetch_source(
    source: "SourceConfig",
    state: "StateStore | None" = None,
) -> tuple[list["Item"], Exception | None]:
    try:
        adapter = _get_adapter(source, state)
        items = adapter.fetch()
        logger.info("source=%s fetched %d items", source.id, len(items))
        return items, None
    except Exception as e:
        logger.error("source=%s fetch error: %s", source.id, e, exc_info=True)
        return [], e


def _get_adapter(source: "SourceConfig", state: "StateStore | None" = None) -> BaseAdapter:
    if source.parse == "federal_register":
        from sweepreader.ingest.federal_register import FederalRegisterAdapter
        return FederalRegisterAdapter(source)
    elif source.parse == "rss_generic":
        from sweepreader.ingest.rss import RssAdapter
        return RssAdapter(source)
    elif source.parse == "miax_alerts":
        from sweepreader.ingest.miax import MiaxAdapter
        return MiaxAdapter(source)
    elif source.parse == "nyse_notifications":
        from sweepreader.ingest.nyse import NyseAdapter
        return NyseAdapter(source)
    elif source.parse == "box_notices":
        from sweepreader.ingest.box import BoxAdapter
        return BoxAdapter(source)
    elif source.parse == "iex_alerts":
        from sweepreader.ingest.iex import IexAdapter
        return IexAdapter(source)
    elif source.parse == "email_html_or_pdf":
        from sweepreader.ingest.email_ingestor import EmailIngestor
        return EmailIngestor(source, state)
    else:
        raise ValueError(f"Unknown parse strategy: {source.parse!r}")
