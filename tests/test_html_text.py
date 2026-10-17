"""The shared selectolax wrapper.

selectolax 1.0 deleted the `modest` backend (`selectolax.parser`), which raises
ImportError on import — it broke every scheduled run until the adapters moved to
`lexbor`. These tests pin the backend so an accidental move back fails here
rather than in CI three hours later.
"""
from __future__ import annotations

import ast
from pathlib import Path

from sweepreader.ingest.html_text import HTMLParser, Node, html_to_text


def test_parser_is_the_lexbor_backend():
    from selectolax.lexbor import LexborHTMLParser, LexborNode
    assert HTMLParser is LexborHTMLParser
    assert Node is LexborNode


def test_no_module_imports_the_removed_modest_backend():
    """Checked by import statement rather than by trying it: the modest backend
    still imports on the older selectolax a dev box may have pinned, and only
    blows up on the version CI resolves."""
    src = Path(__file__).resolve().parents[1] / "src" / "sweepreader"
    offenders = []
    for path in sorted(src.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            if any(n == "selectolax.parser" for n in names):
                offenders.append(str(path.relative_to(src)))
    assert offenders == []


def test_html_to_text_drops_scripts_and_collapses_whitespace():
    html = """
    <html><body>
      <h1>Notice   2026-059</h1>
      <style>.x{color:red}</style>
      <p>Weekend testing\n  dates</p>
      <script>alert(1)</script>
    </body></html>
    """
    assert html_to_text(html) == "Notice 2026-059 Weekend testing dates"


def test_html_to_text_handles_a_bare_fragment():
    assert html_to_text("<p>one</p><p>two</p>") == "one two"


def test_html_to_text_of_empty_input_is_empty():
    assert html_to_text("") == ""


def test_parser_api_used_by_the_adapters():
    """css / css_first / attributes / text(deep=False) — the subset the adapters rely on."""
    tree = HTMLParser(
        '<article class="circulars notice_category-weekend-testing">'
        "<header>Title<div><strong>BOX</strong><span>2026-059</span></div></header>"
        '<footer><a href="/assets/x.pdf">View</a></footer></article>'
    )
    art = tree.css_first("article.circulars")
    assert isinstance(art, Node)
    assert "notice_category-weekend-testing" in (art.attributes.get("class") or "")
    assert art.css_first("header").text(deep=False).strip() == "Title"
    assert [s.text() for s in art.css("header div span")] == ["2026-059"]
    assert art.css_first("footer a").attributes.get("href") == "/assets/x.pdf"
