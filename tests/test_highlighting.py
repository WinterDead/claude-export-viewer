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
from tests.conftest import SYNTHETIC_EXPORT

VENDOR_JS = "/static/vendor/highlightjs/highlight.min.js"
VENDOR_PS_JS = "/static/vendor/highlightjs/powershell.min.js"
VENDOR_CSS = "/static/vendor/highlightjs/github.min.css"
VENDOR_CSS_DARK = "/static/vendor/highlightjs/github-dark.min.css"
VENDOR_LICENSE = "/static/vendor/highlightjs/LICENSE"
INIT_JS = "/static/highlight-init.js"

# Every GET page in the app — base.html supplies the includes on all of them.
ALL_PAGES = (
    "/",
    "/conversations",
    "/projects",
    "/memory",
    "/account",
    "/search",
    "/upload",
    "/inspect",
    "/history",
    "/orphans",
    "/help",
)


@pytest.fixture
def client(export, tmp_path: Path):
    pinned = dataclasses.replace(export, source_paths=[SYNTHETIC_EXPORT])
    app = create_app(pinned, db_path=tmp_path / "viewer.sqlite")
    return app.test_client()


# ---------------------------------------------------------------------------
# Vendored assets are served
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", [VENDOR_JS, VENDOR_PS_JS, VENDOR_CSS, VENDOR_CSS_DARK, VENDOR_LICENSE, INIT_JS]
)
def test_static_asset_served(client, path: str) -> None:
    assert client.get(path).status_code == 200


def test_vendored_license_is_bsd_3_clause(client) -> None:
    """The brief requires the upstream BSD-3-Clause licence to ship alongside
    the bundle for the public repo."""
    body = client.get(VENDOR_LICENSE).get_data(as_text=True)
    assert "BSD 3-Clause License" in body


def test_vendored_bundle_is_the_recorded_version(client) -> None:
    """The vendor README records 11.11.1; the bundle must actually be it."""
    body = client.get(VENDOR_JS).get_data(as_text=True)
    assert "11.11.1" in body


# ---------------------------------------------------------------------------
# Template includes — exactly once per page, in the documented order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", ALL_PAGES)
def test_includes_exactly_once_per_page(client, page: str) -> None:
    r = client.get(page)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # Default (System) theme: both hljs sheets render, media-routed (below).
    for asset in (VENDOR_JS, VENDOR_PS_JS, VENDOR_CSS, VENDOR_CSS_DARK, INIT_JS):
        assert body.count(asset) == 1, f"{asset} appears {body.count(asset)}× on {page}"


def test_include_order_on_a_representative_page(client) -> None:
    body = client.get("/").get_data(as_text=True)
    # Theme CSS (both sheets) before style.css: app styles must win
    # equal-specificity ties.
    assert body.index(VENDOR_CSS) < body.index("/static/style.css")
    assert body.index(VENDOR_CSS_DARK) < body.index("/static/style.css")
    # Bundle before the powershell module (registers into the hljs global)
    # before init: deferred scripts execute in document order.
    assert body.index(VENDOR_JS) < body.index(VENDOR_PS_JS) < body.index(INIT_JS)


# ---------------------------------------------------------------------------
# Theme matrix (v1.3.0 Session T): which sheet loads is media-attribute
# routing, all server-rendered from the viewer_theme cookie. Print always
# gets the light theme — print output is theme-independent.
# ---------------------------------------------------------------------------


def _link_media(body: str, href: str) -> str | None:
    """The media attribute of the stylesheet link for ``href``, ``""`` when
    the link has none, ``None`` when the link is absent."""
    m = re.search(rf'<link rel="stylesheet" href="{re.escape(href)}"([^>]*)>', body)
    if m is None:
        return None
    media = re.search(r'media="([^"]*)"', m.group(1))
    return media.group(1) if media else ""


def test_hljs_theme_matrix_system(client) -> None:
    body = client.get("/").get_data(as_text=True)
    assert _link_media(body, VENDOR_CSS) == "print, screen and (prefers-color-scheme: light)"
    assert _link_media(body, VENDOR_CSS_DARK) == "screen and (prefers-color-scheme: dark)"


def test_hljs_theme_matrix_light(client) -> None:
    client.set_cookie("viewer_theme", "light")
    body = client.get("/").get_data(as_text=True)
    assert _link_media(body, VENDOR_CSS) == ""
    assert _link_media(body, VENDOR_CSS_DARK) is None


def test_hljs_theme_matrix_dark(client) -> None:
    client.set_cookie("viewer_theme", "dark")
    body = client.get("/").get_data(as_text=True)
    assert _link_media(body, VENDOR_CSS) == "print"
    assert _link_media(body, VENDOR_CSS_DARK) == "screen"


def test_vendored_dark_theme_is_the_github_dark_release(client) -> None:
    """Same 11.11.1 cdn-release vendoring as the light theme — the vendor
    README records both; licence unchanged (BSD-3-Clause alongside)."""
    body = client.get(VENDOR_CSS_DARK).get_data(as_text=True)
    assert "github-dark" in body.lower() or ".hljs" in body


# ---------------------------------------------------------------------------
# Init-script wiring — the two choices the browser pass depends on
# ---------------------------------------------------------------------------


def test_init_script_scope_and_mark_guard(client) -> None:
    src = client.get(INIT_JS).get_data(as_text=True)
    # Scope: markdown-rendered containers only, block code only.
    assert ".markdown-body pre code" in src
    # Skip-marks guard: search-hit <mark> tags must never be rebuilt away.
    assert re.search(r"querySelector\(\s*[\"']mark[\"']\s*\)", src)
    # No mutation observers — pages re-render server-side on navigation.
    assert "MutationObserver" not in src
