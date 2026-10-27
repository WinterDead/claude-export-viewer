"""Upload-history log (v1.1 Session A, item A3).

Scenario coverage per the brief:

- Upload records a row with correct counts (and always records, even on a
  duplicate sha — an explicit user action is worth logging).
- Startup with the same sha as the latest row: no new row.
- Startup with a different sha: new row recorded.
- Synthetic-fixture load: nothing recorded.
- ``/history`` renders rows in reverse-chronological order; the empty state
  renders when the table is empty.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sqlite3
from pathlib import Path

import pytest

from app import create_app
from tests.conftest import DUAL_FORMAT_IDS, DUAL_FORMATS, SYNTHETIC_EXPORT, scrape_csrf
from viewer.history import (
    all_history,
    export_counts,
    init_db,
    latest_sha,
    record_load,
    record_startup_load,
)
from viewer.parser import load_export


@pytest.fixture(params=DUAL_FORMATS, ids=DUAL_FORMAT_IDS)
def export_path(request, fixtures_dir: Path) -> Path:
    """Dual-format module (v1.3.0 Session S): export-dependent tests in this
    file run against both container formats."""
    return fixtures_dir / request.param


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_db(c)
    return c


def _history_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as c:
        return c.execute(
            "SELECT source, filename, zip_sha256, conversations, messages,"
            "       projects, project_memories"
            "  FROM upload_history ORDER BY id"
        ).fetchall()


# ---------------------------------------------------------------------------
# Unit: schema + record/query functions against an in-memory DB
# ---------------------------------------------------------------------------


def test_init_db_is_idempotent(conn) -> None:
    init_db(conn)
    init_db(conn)
    assert latest_sha(conn) is None


def test_record_load_stores_counts_from_export(conn, export) -> None:
    record_load(
        conn,
        source="upload",
        filename="export.zip",
        zip_sha256="ab" * 32,
        export=export,
    )
    rows = all_history(conn)
    assert len(rows) == 1
    row = rows[0]
    convs, msgs, projs, mems = export_counts(export)
    assert (row.conversations, row.messages, row.projects, row.project_memories) == (
        convs,
        msgs,
        projs,
        mems,
    )
    assert row.source == "upload"
    assert row.filename == "export.zip"
    assert row.recorded_at is not None  # ISO timestamp parsed back


def test_startup_same_sha_as_latest_row_records_nothing(conn, export) -> None:
    assert record_startup_load(conn, filename="a.zip", zip_sha256="aa" * 32, export=export)
    assert not record_startup_load(conn, filename="a.zip", zip_sha256="aa" * 32, export=export)
    assert len(all_history(conn)) == 1


def test_startup_different_sha_records_new_row(conn, export) -> None:
    record_startup_load(conn, filename="a.zip", zip_sha256="aa" * 32, export=export)
    assert record_startup_load(conn, filename="b.zip", zip_sha256="bb" * 32, export=export)
    assert len(all_history(conn)) == 2
    assert latest_sha(conn) == "bb" * 32


def test_unparseable_timestamp_is_preserved_and_flagged(conn, export, caplog) -> None:
    record_load(conn, source="upload", filename="x.zip", zip_sha256="cc" * 32, export=export)
    conn.execute("UPDATE upload_history SET recorded_at_utc = 'not-a-date'")
    conn.commit()
    with caplog.at_level("WARNING", logger="viewer.history"):
        rows = all_history(conn)
    assert len(rows) == 1  # row survives
    assert rows[0].recorded_at is None
    assert rows[0].recorded_at_raw == "not-a-date"
    assert any("unparseable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Integration: upload route records a row
# ---------------------------------------------------------------------------


def _upload(client, zip_bytes: bytes, filename: str) -> None:
    token = scrape_csrf(client)
    r = client.post(
        "/upload",
        data={"csrf_token": token, "export_files": (io.BytesIO(zip_bytes), filename)},
        content_type="multipart/form-data",
    )
    assert r.status_code == 302


def test_upload_records_row_with_correct_counts(
    app_factory, fixtures_dir: Path, tmp_path: Path
) -> None:
    flask_app = app_factory()
    zip_bytes = (fixtures_dir / "bad_dangling_memory.zip").read_bytes()
    _upload(flask_app.test_client(), zip_bytes, "my-export.zip")

    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    assert len(rows) == 1
    source, filename, sha, convs, msgs, projs, mems = rows[0]
    assert source == "upload"
    assert filename == "my-export.zip"
    # The recorded sha is the same digest that keys the pickle cache.
    assert sha == hashlib.sha256(zip_bytes).hexdigest()
    expected = export_counts(load_export(fixtures_dir / "bad_dangling_memory.zip"))
    assert (convs, msgs, projs, mems) == expected


def test_upload_always_records_even_on_duplicate_sha(
    app_factory, fixtures_dir: Path, tmp_path: Path
) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    zip_bytes = (fixtures_dir / "bad_dangling_memory.zip").read_bytes()
    _upload(client, zip_bytes, "first.zip")
    _upload(client, zip_bytes, "second.zip")

    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    assert len(rows) == 2
    assert rows[0][2] == rows[1][2]  # identical sha, both recorded


# ---------------------------------------------------------------------------
# Integration: startup recording through create_app
# ---------------------------------------------------------------------------


def test_startup_with_active_zip_records_once_per_archive(
    tmp_path: Path, fixtures_dir: Path, monkeypatch
) -> None:
    import app as app_module

    active = tmp_path / "cache" / "active_export.zip"
    active.parent.mkdir(parents=True)
    shutil.copyfile(fixtures_dir / SYNTHETIC_EXPORT, active)

    monkeypatch.setattr(app_module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app_module, "ACTIVE_SET", tmp_path / "cache" / "active_export")
    monkeypatch.setattr(app_module, "ACTIVE_ZIP", active)
    monkeypatch.setattr(app_module, "PARSE_CACHE", tmp_path / "cache" / "parsed_export.pkl")
    db_path = tmp_path / "viewer.sqlite"

    # First launch: records. Relaunch with the same archive: no new row.
    create_app(None, db_path=db_path)
    create_app(None, db_path=db_path)
    rows = _history_rows(db_path)
    assert len(rows) == 1
    assert rows[0][0] == "startup"
    assert rows[0][1] == "active_export.zip"

    # Replace the archive with different bytes: relaunch records a new row.
    shutil.copyfile(fixtures_dir / "bad_dangling_memory.zip", active)
    create_app(None, db_path=db_path)
    rows = _history_rows(db_path)
    assert len(rows) == 2
    assert rows[1][0] == "startup"


def test_synthetic_fixture_startup_records_nothing(tmp_path: Path, fixtures_dir: Path) -> None:
    db_path = tmp_path / "viewer.sqlite"
    create_app(fixtures_dir / SYNTHETIC_EXPORT, db_path=db_path)
    assert _history_rows(db_path) == []


# ---------------------------------------------------------------------------
# /history page
# ---------------------------------------------------------------------------


def test_history_page_empty_state(app_factory) -> None:
    client = app_factory().test_client()
    body = client.get("/history").get_data(as_text=True)
    assert "No uploads recorded yet" in body
    assert "<table" not in body


def test_history_page_renders_rows_reverse_chronologically(app_factory, fixtures_dir: Path) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    _upload(client, (fixtures_dir / SYNTHETIC_EXPORT).read_bytes(), "older.zip")
    _upload(client, (fixtures_dir / "bad_dangling_memory.zip").read_bytes(), "newer.zip")

    body = client.get("/history").get_data(as_text=True)
    assert "No uploads recorded yet" not in body
    # Most recent upload first.
    assert body.index("newer.zip") < body.index("older.zip")
    # Source badge, short sha (12 chars) with the full hash in a title attr.
    assert ">Upload</span>" in body
    full_sha = hashlib.sha256((fixtures_dir / "bad_dangling_memory.zip").read_bytes()).hexdigest()
    assert f'title="{full_sha}">{full_sha[:12]}</code>' in body


def test_history_link_in_nav(app_factory) -> None:
    client = app_factory().test_client()
    body = client.get("/").get_data(as_text=True)
    assert '<a href="/history">History</a>' in body


def test_nav_order_history_directly_after_upload(app_factory) -> None:
    """Operator decision (v1.2.5 Session O, B1): History records uploads, so
    it sits directly right of Upload — Home … Upload, History, Inspect, Help."""
    client = app_factory().test_client()
    body = client.get("/").get_data(as_text=True)
    tail = [">Upload<", ">History<", ">Inspect<", ">Help<"]
    positions = [body.index(marker) for marker in tail]
    assert positions == sorted(positions)
