"""Search syntax guide (v1.1 Session A, item A2).

The collapsed <details class="syntax-guide"> block must be present in all
three search-page states — empty query, results shown, and syntax error —
because the error state is where the guide is needed most.
"""

from __future__ import annotations

GUIDE_MARKER = '<details class="syntax-guide">'
TIP_FRAGMENT = "handled automatically"


def test_guide_present_on_empty_query(client) -> None:
    body = client.get("/search").get_data(as_text=True)
    assert GUIDE_MARKER in body
    assert TIP_FRAGMENT in body
    assert "Search syntax guide" in body


def test_guide_present_with_results(client) -> None:
    body = client.get("/search?q=hello").get_data(as_text=True)
    assert GUIDE_MARKER in body


def test_guide_present_on_syntax_error(client) -> None:
    # Strict syntax mode with unquoted punctuation — a guaranteed FTS5
    # syntax error (also documented as the example in the guide itself).
    body = client.get("/search?q=B.Sc.%20AND%20engineering").get_data(as_text=True)
    assert "Query syntax error" in body
    assert GUIDE_MARKER in body


def test_guide_is_collapsed_by_default(client) -> None:
    body = client.get("/search").get_data(as_text=True)
    assert '<details class="syntax-guide" open' not in body
