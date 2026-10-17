"""Shared HTML parsing and HTML→text extraction built on selectolax.

Used by the scrape/API adapters to turn notice bodies into clean classifier
input, replacing hand-rolled regex stripping.

The parser types are re-exported from here so there is a single place that names
a selectolax backend. selectolax 1.0 removed the old `modest` backend
(`selectolax.parser`) and raises ImportError on import, so every adapter uses
the `lexbor` backend instead. The API surface we rely on — `css`, `css_first`,
`text(separator=, deep=)`, `attributes`, `decompose`, `body`, `root` — is
identical between the two.
"""
from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

__all__ = ["HTMLParser", "Node", "html_to_text"]

_DROP = "script, style, svg, noscript, template"


def html_to_text(html: str) -> str:
    """Visible text of an HTML fragment, whitespace-collapsed."""
    if not html:
        return ""
    tree = HTMLParser(html)
    for node in tree.css(_DROP):
        node.decompose()
    root = tree.body or tree.root
    text = root.text(separator=" ") if root else ""
    return " ".join(text.split())
