from sweepreader.ingest.base import BaseAdapter, fetch_source
from sweepreader.ingest.rss import RssAdapter
from sweepreader.ingest.federal_register import FederalRegisterAdapter

__all__ = ["BaseAdapter", "fetch_source", "RssAdapter", "FederalRegisterAdapter"]
