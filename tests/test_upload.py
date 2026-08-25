"""Route + flow tests for upload (plan §12 step 7).

Covers: empty upload rejected, non-zip rejected, malformed zip surfaces a
friendly error and leaves the previous archive in place, happy-path replace
with re-indexing, parse cache invalidation on upload.
"""

from __future__ import annotations

import io
from pathlib import Path

from tests.conftest import SYNTHETIC_EXPORT, scrape_csrf

# ---------------------------------------------------------------------------
# Form rendering
# ---------------------------------------------------------------------------


def test_upload_form_renders(app_factory) -> None:
    client = app_factory().test_client()
    body = client.get("/upload").get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in body
    assert 'name="export_zip"' in body
    assert 'name="csrf_token"' in body


# ---------------------------------------------------------------------------
# Validation: missing / wrong extension / malformed zip
# ---------------------------------------------------------------------------


def test_upload_rejects_empty_submission(app_factory) -> None:
    client = app_factory().test_client()
    token = scrape_csrf(client)
    r = client.post(
        "/upload",
        data={"csrf_token": token},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert "No file selected" in r.get_data(as_text=True)


def test_upload_rejects_non_zip_extension(app_factory) -> None:
    client = app_factory().test_client()
    token = scrape_csrf(client)
    r = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_zip": (io.BytesIO(b"not a zip"), "evil.txt"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert "must be a .zip" in r.get_data(as_text=True)


def test_upload_rejects_malformed_zip_keeps_previous_export(app_factory) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()

    before_export = flask_app.config["EXPORT"]
    token = scrape_csrf(client)

    r = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_zip": (io.BytesIO(b"not really a zip"), "broken.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    # Apostrophe is HTML-escaped by Jinja, so match a fragment that survives.
    assert "parse archive" in r.get_data(as_text=True)
    # Active export must NOT have changed.
    assert flask_app.config["EXPORT"] is before_export
    # And no zip should have been written into place.
    assert not flask_app.config["ACTIVE_ZIP_PATH"].exists()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_upload_happy_path_replaces_export(app_factory, fixtures_dir: Path) -> None:
    flask_app = app_factory(SYNTHETIC_EXPORT)
    client = flask_app.test_client()
    token = scrape_csrf(client)

    # Upload bad_dangling_memory.zip — it carries an orphan project memory
    # that wasn't in the synthetic fixture, so we can detect the swap.
    new_zip_bytes = (fixtures_dir / "bad_dangling_memory.zip").read_bytes()
    r = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_zip": (io.BytesIO(new_zip_bytes), "uploaded.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/")

    # Active export now reflects the uploaded archive.
    assert len(flask_app.config["EXPORT"].orphan_project_memories) >= 1
    assert flask_app.config["ACTIVE_ZIP_PATH"].is_file()


def test_upload_invalidates_parse_cache(app_factory, fixtures_dir: Path) -> None:
    """A pre-existing parse cache must be removed on upload — the next
    startup will repickle from the new zip rather than serving stale."""
    flask_app = app_factory(SYNTHETIC_EXPORT)
    cache_path: Path = flask_app.config["PARSE_CACHE_PATH"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"stale pickle")
    assert cache_path.exists()

    client = flask_app.test_client()
    token = scrape_csrf(client)
    new_zip_bytes = (fixtures_dir / "bad_dangling_memory.zip").read_bytes()
    client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_zip": (io.BytesIO(new_zip_bytes), "uploaded.zip"),
        },
        content_type="multipart/form-data",
    )
    assert not cache_path.exists()


def test_upload_rebuilds_search_index(app_factory, fixtures_dir: Path) -> None:
    """After upload, /search should hit content from the NEW archive,
    not the old. The dangling-memory fixture has an orphan body starting
    with '**Orphan memory**' which the synthetic fixture doesn't."""
    flask_app = app_factory(SYNTHETIC_EXPORT)
    client = flask_app.test_client()

    # Confirm the term isn't findable yet.
    body = client.get("/search?q=Orphan").get_data(as_text=True)
    assert "Orphan memories" not in body  # nothing matches; empty state

    token = scrape_csrf(client)
    new_zip_bytes = (fixtures_dir / "bad_dangling_memory.zip").read_bytes()
    client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_zip": (io.BytesIO(new_zip_bytes), "uploaded.zip"),
        },
        content_type="multipart/form-data",
    )
    body = client.get("/search?q=%22Orphan+memory%22").get_data(as_text=True)
    # The orphan body matches against memory_project (orphan).
    assert "<mark>" in body  # something highlighted
