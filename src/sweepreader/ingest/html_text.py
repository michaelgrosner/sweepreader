"""Shared HTML→text extraction built on selectolax (fast lexbor backend).

Used by the scrape/API adapters to turn notice bodies into clean classifier
input, replacing hand-rolled regex stripping.
"""
from __future__ import annotations

from selectolax.parser import HTMLParser

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
