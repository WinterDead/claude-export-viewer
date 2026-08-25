"""Shared fixtures and helpers for the whole suite (v1.3.0 Session S).

``export_path`` is the container-format seam: it defaults to the legacy
happy-path ZIP and can be pointed at any other fixture container by indirect
parametrisation::

    @pytest.mark.parametrize("export_path", ["bad_dangling_memory.zip"], indirect=True)

Everything downstream (``export``, ``app``, ``client``) rides it, so a test
family opts into another container — including a current-format set directory
under ``tests/fixtures/`` — without touching its own body.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import create_app
from viewer.parser import Export, load_export

#: The legacy happy-path container — the suite-wide default.
SYNTHETIC_EXPORT = "synthetic_export.zip"


def scrape_csrf(client, page: str = "/upload") -> str:
    """Pull a valid CSRF token from the rendered form on ``page``."""
    body = client.get(page).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert m, f"no csrf_token in a rendered form on {page}"
    return m.group(1)


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def export_path(request, fixtures_dir: Path) -> Path:
    """Path to the export container under test (see module docstring)."""
    name = getattr(request, "param", SYNTHETIC_EXPORT)
    return fixtures_dir / name


@pytest.fixture
def export(export_path: Path) -> Export:
    """The parsed ``Export`` model for ``export_path``."""
    return load_export(export_path)


@pytest.fixture
def app(export_path: Path, tmp_path: Path):
    """App over ``export_path`` with an isolated SQLite under ``tmp_path``."""
    return create_app(export_path, db_path=tmp_path / "viewer.sqlite")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_factory(tmp_path: Path, fixtures_dir: Path, monkeypatch):
    """Builder for an app whose writable paths (active export, parse cache,
    ``viewer.sqlite``) all live under ``tmp_path/cache``, so a snapshot of
    that directory covers the complete on-disk state the app can write
    (Session K pattern). Module globals are monkeypatched, so they are
    restored after each test.
    """

    def _build(initial_fixture: str = SYNTHETIC_EXPORT):
        import app as app_module

        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(app_module, "CACHE_DIR", cache_dir)
        monkeypatch.setattr(app_module, "ACTIVE_ZIP", cache_dir / "active_export.zip")
        monkeypatch.setattr(app_module, "PARSE_CACHE", cache_dir / "parsed_export.pkl")
        monkeypatch.setattr(app_module, "REAL_EXPORT", tmp_path / "data" / "data.zip")

        flask_app = create_app(
            fixtures_dir / initial_fixture,
            db_path=cache_dir / "viewer.sqlite",
        )
        flask_app.config["ACTIVE_ZIP_PATH"] = app_module.ACTIVE_ZIP
        flask_app.config["PARSE_CACHE_PATH"] = app_module.PARSE_CACHE
        return flask_app

    return _build
