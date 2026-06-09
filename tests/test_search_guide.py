"""Search syntax guide (v1.1 Session A, item A2).

The collapsed <details class="syntax-guide"> block must be present in all
three search-page states — empty query, results shown, and syntax error —
because the error state is where the guide is needed most.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app

GUIDE_MARKER = '<details class="syntax-guide">'
TIP_FRAGMENT = "handled automatically"


@pytest.fixture
def synthetic_client(tmp_path: Path, fixtures_dir: Path):
    app = create_app(
        fixtures_dir / "synthetic_export.zip",
        db_path=tmp_path / "viewer.sqlite",
    )
    return app.test_client()


def test_guide_present_on_empty_query(synthetic_client) -> None:
    body = synthetic_client.get("/search").get_data(as_text=True)
    assert GUIDE_MARKER in body
    assert TIP_FRAGMENT in body
    assert "Search syntax guide" in body


def test_guide_present_with_results(synthetic_client) -> None:
    body = synthetic_client.get("/search?q=hello").get_data(as_text=True)
    assert GUIDE_MARKER in body


def test_guide_present_on_syntax_error(synthetic_client) -> None:
    # Strict syntax mode with unquoted punctuation — a guaranteed FTS5
    # syntax error (also documented as the example in the guide itself).
    body = synthetic_client.get("/search?q=B.Sc.%20AND%20engineering").get_data(as_text=True)
    assert "Query syntax error" in body
    assert GUIDE_MARKER in body


def test_guide_is_collapsed_by_default(synthetic_client) -> None:
    body = synthetic_client.get("/search").get_data(as_text=True)
    assert '<details class="syntax-guide" open' not in body
