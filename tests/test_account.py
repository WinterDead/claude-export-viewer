"""Account page (v1.3.0 Session S Phase 4, record §8, copy C4).

Present whenever ``users.json`` is in the viewed set — both formats; the
login-history table is current-format content with the authored empty state
otherwise. Nav entry hidden (and the route 404) when no account file is
loaded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import create_app
from tests.conftest import DUAL_FORMAT_IDS, DUAL_FORMATS


@pytest.fixture(params=DUAL_FORMATS, ids=DUAL_FORMAT_IDS)
def export_path(request, fixtures_dir: Path) -> Path:
    """Dual-format module (v1.3.0 Session S): export-dependent tests in this
    file run against both container formats."""
    return fixtures_dir / request.param


@pytest.fixture
def current_client(fixtures_dir: Path, tmp_path: Path):
    app = create_app(fixtures_dir / "current", db_path=tmp_path / "viewer-current.sqlite")
    return app.test_client()


@pytest.fixture
def no_account_client(fixtures_dir: Path, tmp_path: Path):
    """A subset without light_metadata — no ``users.json`` in the set."""
    app = create_app(
        fixtures_dir / "current" / "conversations-000.zip",
        db_path=tmp_path / "viewer-subset.sqlite",
    )
    return app.test_client()


# ---------------------------------------------------------------------------
# Presence rules
# ---------------------------------------------------------------------------


def test_nav_shows_account_when_users_json_loaded(client) -> None:
    body = client.get("/").get_data(as_text=True)
    assert '<a href="/account">Account</a>' in body


def test_nav_hides_account_without_users_json(no_account_client) -> None:
    body = no_account_client.get("/").get_data(as_text=True)
    assert 'href="/account"' not in body


def test_account_route_404_without_users_json(no_account_client) -> None:
    assert no_account_client.get("/account").status_code == 404


# ---------------------------------------------------------------------------
# Page content — headings, identity fields, table (C4 copy byte-exact)
# ---------------------------------------------------------------------------


def test_account_page_headings_and_identity(client) -> None:
    r = client.get("/account")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "<h1>Account</h1>" in body
    assert "<h2>Login history</h2>" in body
    assert "Synthetic User" in body
    assert "synthetic@example.invalid" in body
    assert "00000000-0000-0000-0000-000000000001" in body


def test_login_history_table_headers_are_c4_copy(current_client) -> None:
    body = current_client.get("/account").get_data(as_text=True)
    for header in ("Timestamp", "Method", "IP address", "Location", "Browser &amp; OS"):
        assert f"<th>{header}</th>" in body


def test_login_history_rows_render_condensed_cells(current_client) -> None:
    body = current_client.get("/account").get_data(as_text=True)
    assert "203.0.113.10" in body
    assert "magic_link" in body
    # Browser & OS condensed from the user-agent fields.
    assert "Firefox 142.0 · Windows 11" in body
    # Null city/region collapse to the country alone.
    assert "<td>US</td>" in body
    # A null os_version drops cleanly.
    assert "Chrome 127.0 · Linux" in body


def test_login_history_sorted_newest_first(current_client) -> None:
    """The fixture's events are deliberately unordered (newest is the middle
    entry); the table must render newest first."""
    body = current_client.get("/account").get_data(as_text=True)
    ips = re.findall(r"<code>(\d+\.\d+\.\d+\.\d+)</code>", body)
    # Aug 20 (198.51.100.7), Aug 10 (192.0.2.55), Aug 1 (203.0.113.10).
    assert ips == ["198.51.100.7", "192.0.2.55", "203.0.113.10"]


def test_legacy_export_shows_empty_state_line(client, export_path: Path) -> None:
    if export_path.is_dir():
        pytest.skip("current-format run — the table case is covered above")
    body = client.get("/account").get_data(as_text=True)
    # Appendix C4, byte-exact.
    assert "This export doesn't include login history." in body
    assert "<table" not in body
