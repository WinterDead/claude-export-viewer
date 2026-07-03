"""Graceful-startup tests (v1.1.5 Session I, Task C).

The stored upload (``cache/active_export.zip``) and the dev copy
(``data/data.zip``) fall through the startup load order on a parse failure:
a ``WARNING`` names the skipped source, the file is left in place, and — for
the stored upload only — every page shows a persistent banner until a
successful upload replaces the file. A broken fixture still raises: it is
committed code, so its failure is a broken build, not a user condition.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as app_module
from app import create_app

# The banner copy is chat-authored and ships byte-exact (Session I brief,
# Task C). The template wraps it across two source lines, so assertions
# compare whitespace-normalised text against this single-line form.
BANNER_TEXT = (
    "Your stored export couldn't be parsed and wasn't loaded. The file was left in place — "
    "upload a replacement from the Upload page to get back to your data."
)

BANNER_RE = re.compile(r'<aside class="load-error-banner"[^>]*>(.*?)</aside>', re.S)


@pytest.fixture
def startup_env(tmp_path: Path, fixtures_dir: Path, monkeypatch) -> SimpleNamespace:
    """Point app.py's module-level source paths into ``tmp_path``.

    Tests populate the active/dev files they need, then call
    ``create_app(None)`` to exercise the real startup load order without
    touching the repo's ``cache/`` or ``data/`` directories. The synthetic
    fixture path is deliberately left real — the committed fixture is the
    last-ditch fallback under test.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    active = cache_dir / "active_export.zip"
    dev_copy = data_dir / "data.zip"
    monkeypatch.setattr(app_module, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(app_module, "ACTIVE_ZIP", active)
    monkeypatch.setattr(app_module, "PARSE_CACHE", cache_dir / "parsed_export.pkl")
    monkeypatch.setattr(app_module, "REAL_EXPORT", dev_copy)
    return SimpleNamespace(
        active=active,
        dev_copy=dev_copy,
        db_path=tmp_path / "viewer.sqlite",
        corrupt_bytes=(fixtures_dir / "corrupt_export.zip").read_bytes(),
        good_bytes=(fixtures_dir / "synthetic_export.zip").read_bytes(),
    )


def _banner_text(body: str) -> str | None:
    m = BANNER_RE.search(body)
    if m is None:
        return None
    return " ".join(m.group(1).split())


def _csrf_token(client) -> str:
    body = client.get("/upload").get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert m
    return m.group(1)


# ---------------------------------------------------------------------------
# Corrupt-zip fixture (target-state item 8)
# ---------------------------------------------------------------------------


def test_corrupt_fixture_is_not_a_zip(fixtures_dir: Path) -> None:
    """The corrupt fixture must fail at the zipfile layer, not the JSON
    layer — synthetic bytes without the zip magic."""
    path = fixtures_dir / "corrupt_export.zip"
    assert path.is_file()
    assert not zipfile.is_zipfile(path)


# ---------------------------------------------------------------------------
# Corrupt stored export → fixture + banner (items 1–3, 6)
# ---------------------------------------------------------------------------


def test_corrupt_stored_export_falls_back_to_fixture(startup_env) -> None:
    startup_env.active.write_bytes(startup_env.corrupt_bytes)

    flask_app = create_app(None, db_path=startup_env.db_path)

    # The fixture loaded, not the corrupt archive.
    loaded = [Path(p).name for p in flask_app.config["EXPORT"].source_paths]
    assert loaded == ["synthetic_export.zip"]
    assert flask_app.config["STORED_EXPORT_FAILED"] is True

    # The corrupt file was left exactly where it was, byte-identical.
    assert startup_env.active.is_file()
    assert startup_env.active.read_bytes() == startup_env.corrupt_bytes


def test_banner_present_on_home_conversations_and_help(startup_env) -> None:
    startup_env.active.write_bytes(startup_env.corrupt_bytes)
    client = create_app(None, db_path=startup_env.db_path).test_client()

    for page in ("/", "/conversations", "/help"):
        body = client.get(page).get_data(as_text=True)
        assert _banner_text(body) is not None, f"banner missing on {page}"


def test_banner_text_matches_authored_copy(startup_env) -> None:
    startup_env.active.write_bytes(startup_env.corrupt_bytes)
    client = create_app(None, db_path=startup_env.db_path).test_client()

    body = client.get("/").get_data(as_text=True)
    assert _banner_text(body) == BANNER_TEXT


def test_fallthrough_logs_warning_naming_stored_source(startup_env, caplog) -> None:
    startup_env.active.write_bytes(startup_env.corrupt_bytes)

    with caplog.at_level(logging.WARNING, logger="app"):
        create_app(None, db_path=startup_env.db_path)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "active_export.zip" in r.getMessage() and "skipped" in r.getMessage() for r in warnings
    )


# ---------------------------------------------------------------------------
# Recovery: a successful upload replaces the file and clears the banner
# (item 4)
# ---------------------------------------------------------------------------


def test_successful_upload_clears_banner_and_replaces_file(startup_env) -> None:
    startup_env.active.write_bytes(startup_env.corrupt_bytes)
    flask_app = create_app(None, db_path=startup_env.db_path)
    client = flask_app.test_client()
    assert _banner_text(client.get("/").get_data(as_text=True)) is not None

    token = _csrf_token(client)
    r = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_zip": (io.BytesIO(startup_env.good_bytes), "replacement.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 302

    # New export loaded, stored file replaced, banner gone.
    assert flask_app.config["STORED_EXPORT_FAILED"] is False
    assert startup_env.active.read_bytes() == startup_env.good_bytes
    assert _banner_text(client.get("/").get_data(as_text=True)) is None


# ---------------------------------------------------------------------------
# Regression guards (items 5, 7)
# ---------------------------------------------------------------------------


def test_healthy_stored_export_shows_no_banner(startup_env) -> None:
    startup_env.active.write_bytes(startup_env.good_bytes)
    flask_app = create_app(None, db_path=startup_env.db_path)

    loaded = [Path(p).name for p in flask_app.config["EXPORT"].source_paths]
    assert loaded == ["active_export.zip"]
    assert flask_app.config["STORED_EXPORT_FAILED"] is False

    body = flask_app.test_client().get("/").get_data(as_text=True)
    assert _banner_text(body) is None


def test_corrupt_dev_copy_falls_back_silently(startup_env, caplog) -> None:
    """No stored upload, corrupt dev copy: fixture loads, WARNING logged,
    but no banner — the dev copy is a developer convenience that public
    users never have."""
    startup_env.dev_copy.write_bytes(startup_env.corrupt_bytes)

    with caplog.at_level(logging.WARNING, logger="app"):
        flask_app = create_app(None, db_path=startup_env.db_path)

    loaded = [Path(p).name for p in flask_app.config["EXPORT"].source_paths]
    assert loaded == ["synthetic_export.zip"]
    assert flask_app.config["STORED_EXPORT_FAILED"] is False

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("data.zip" in r.getMessage() and "skipped" in r.getMessage() for r in warnings)

    body = flask_app.test_client().get("/").get_data(as_text=True)
    assert _banner_text(body) is None
