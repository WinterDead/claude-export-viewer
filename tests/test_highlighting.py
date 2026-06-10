"""Syntax-highlighting wiring (v1.1 Session F).

Server-side tests can't execute JS, so the highlighting behaviour itself is
browser-pass territory (see docs/briefs/v1.1-session-f-verification.md).
What CAN be pinned here:

- The vendored highlight.js assets (bundle, theme, LICENSE) are served.
- Every page includes the stylesheet and both scripts exactly once, in the
  load order base.html documents (theme before style.css so app styles win
  equal-specificity ties; bundle before init because deferred scripts run in
  document order).
- The init script's two load-bearing choices — the ``.markdown-body pre
  code`` scope and the skip-``<mark>`` guard — are present in the source.

The ``class`` sanitiser boundary the highlighter depends on is covered in
``tests/test_render.py``.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from app import create_app
from viewer.parser import load_export

VENDOR_JS = "/static/vendor/highlightjs/highlight.min.js"
VENDOR_PS_JS = "/static/vendor/highlightjs/powershell.min.js"
VENDOR_CSS = "/static/vendor/highlightjs/github.min.css"
VENDOR_LICENSE = "/static/vendor/highlightjs/LICENSE"
INIT_JS = "/static/highlight-init.js"

# Every GET page in the app — base.html supplies the includes on all of them.
ALL_PAGES = (
    "/",
    "/conversations",
    "/projects",
    "/memory",
    "/search",
    "/upload",
    "/history",
    "/orphans",
    "/help",
)


@pytest.fixture
def synthetic_client(tmp_path: Path, fixtures_dir: Path):
    export = load_export(fixtures_dir / "synthetic_export.zip")
    export = dataclasses.replace(export, source_paths=["synthetic_export.zip"])
    app = create_app(export, db_path=tmp_path / "viewer.sqlite")
    return app.test_client()


# ---------------------------------------------------------------------------
# Vendored assets are served
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [VENDOR_JS, VENDOR_PS_JS, VENDOR_CSS, VENDOR_LICENSE, INIT_JS])
def test_static_asset_served(synthetic_client, path: str) -> None:
    assert synthetic_client.get(path).status_code == 200


def test_vendored_license_is_bsd_3_clause(synthetic_client) -> None:
    """The brief requires the upstream BSD-3-Clause licence to ship alongside
    the bundle for the public repo."""
    body = synthetic_client.get(VENDOR_LICENSE).get_data(as_text=True)
    assert "BSD 3-Clause License" in body


def test_vendored_bundle_is_the_recorded_version(synthetic_client) -> None:
    """The vendor README records 11.11.1; the bundle must actually be it."""
    body = synthetic_client.get(VENDOR_JS).get_data(as_text=True)
    assert "11.11.1" in body


# ---------------------------------------------------------------------------
# Template includes — exactly once per page, in the documented order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", ALL_PAGES)
def test_includes_exactly_once_per_page(synthetic_client, page: str) -> None:
    r = synthetic_client.get(page)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    for asset in (VENDOR_JS, VENDOR_PS_JS, VENDOR_CSS, INIT_JS):
        assert body.count(asset) == 1, f"{asset} appears {body.count(asset)}× on {page}"


def test_include_order_on_a_representative_page(synthetic_client) -> None:
    body = synthetic_client.get("/").get_data(as_text=True)
    # Theme CSS before style.css: app styles must win equal-specificity ties.
    assert body.index(VENDOR_CSS) < body.index("/static/style.css")
    # Bundle before the powershell module (registers into the hljs global)
    # before init: deferred scripts execute in document order.
    assert body.index(VENDOR_JS) < body.index(VENDOR_PS_JS) < body.index(INIT_JS)


# ---------------------------------------------------------------------------
# Init-script wiring — the two choices the browser pass depends on
# ---------------------------------------------------------------------------


def test_init_script_scope_and_mark_guard(synthetic_client) -> None:
    src = synthetic_client.get(INIT_JS).get_data(as_text=True)
    # Scope: markdown-rendered containers only, block code only.
    assert ".markdown-body pre code" in src
    # Skip-marks guard: search-hit <mark> tags must never be rebuilt away.
    assert re.search(r"querySelector\(\s*[\"']mark[\"']\s*\)", src)
    # No mutation observers — pages re-render server-side on navigation.
    assert "MutationObserver" not in src
