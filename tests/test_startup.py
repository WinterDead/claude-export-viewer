"""Graceful-startup tests (v1.1.5 Session I, Task C).

The stored upload (``cache/active_export/`` or the pre-set
``cache/active_export.zip``) falls through the startup load order on a parse
failure: a ``WARNING`` names the skipped source, the file is left in place,
and every page shows a persistent banner until a successful upload replaces
the file. Since v1.3.1 the order is uploaded export → built-in fixture; the
``data/`` development copy is retired. A broken fixture still raises: it is
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
from tests.conftest import SYNTHETIC_EXPORT, scrape_csrf

# The banner copy is chat-authored and ships byte-exact (Session I brief,
# Task C). The template wraps it across two source lines, so assertions
# compare whitespace-normalised text against this single-line form.
BANNER_TEXT = (
    "Your stored export couldn't be parsed and wasn't loaded. The file was left in place — "
    "upload a replacement from the Upload page to get back to your data."
)

BANNER_RE = re.compile(r'<aside class="load-error-banner"[^>]*>(.*?)</aside>', re.S)


@pytest.fixture
def startup_env(
    tmp_path: Path, fixtures_dir: Path, export_path: Path, monkeypatch
) -> SimpleNamespace:
    """Point app.py's module-level source paths into ``tmp_path``.

    Tests populate the stored files they need, then call
    ``create_app(None)`` to exercise the real startup load order without
    touching the repo's ``cache/`` directory. The synthetic
    fixture path is deliberately left real — the committed fixture is the
    last-ditch fallback under test.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    active_set = cache_dir / "active_export"
    active = cache_dir / "active_export.zip"
    monkeypatch.setattr(app_module, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(app_module, "ACTIVE_SET", active_set)
    monkeypatch.setattr(app_module, "ACTIVE_ZIP", active)
    monkeypatch.setattr(app_module, "PARSE_CACHE", cache_dir / "parsed_export.pkl")
    return SimpleNamespace(
        active_set=active_set,
        active=active,
        db_path=tmp_path / "viewer.sqlite",
        corrupt_bytes=(fixtures_dir / "corrupt_export.zip").read_bytes(),
        good_bytes=export_path.read_bytes(),
    )


def _banner_text(body: str) -> str | None:
    m = BANNER_RE.search(body)
    if m is None:
        return None
    return " ".join(m.group(1).split())


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
    assert loaded == [SYNTHETIC_EXPORT]
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

    token = scrape_csrf(client)
    r = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_files": (io.BytesIO(startup_env.good_bytes), "replacement.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 302

    # New export loaded into the set directory (v1.3.0, record §5), the
    # unparseable pre-set zip retired, banner gone.
    assert flask_app.config["STORED_EXPORT_FAILED"] is False
    assert (startup_env.active_set / "replacement.zip").read_bytes() == startup_env.good_bytes
    assert not startup_env.active.exists()
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


def test_fixture_loads_when_no_set_is_stored(startup_env, caplog) -> None:
    """v1.3.1: the load order is uploaded export → built-in fixture. With
    nothing stored, the fixture loads quietly — no banner, no warning, no
    history row."""
    with caplog.at_level(logging.WARNING, logger="app"):
        flask_app = create_app(None, db_path=startup_env.db_path)

    loaded = [Path(p).name for p in flask_app.config["EXPORT"].source_paths]
    assert loaded == [SYNTHETIC_EXPORT]
    assert flask_app.config["STORED_EXPORT_FAILED"] is False
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    body = flask_app.test_client().get("/").get_data(as_text=True)
    assert _banner_text(body) is None


def test_data_dev_copy_step_is_retired() -> None:
    """The ``data/`` development copy no longer exists in the load order —
    real exports never live in the project tree (record D12)."""
    assert not hasattr(app_module, "REAL_EXPORT")
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert '"data"' not in source


def test_importing_app_builds_no_app() -> None:
    """Importing the module must not run the startup load order: the suite
    imports it, and an import must never parse or index the user's stored
    export under ``cache/``. ``flask --app app`` finds the factory instead —
    Flask's discovery calls ``create_app`` when it takes no required
    arguments."""
    import inspect

    assert not hasattr(app_module, "app")
    params = inspect.signature(app_module.create_app).parameters.values()
    assert all(p.default is not inspect.Parameter.empty for p in params)
