"""Unit tests for ``viewer.tagging`` against an in-memory SQLite database."""

from __future__ import annotations

import sqlite3

import pytest

from viewer.tagging import all_tags, get_tag, init_db, set_tag, tags_for_project


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_db(c)
    return c


def test_init_db_is_idempotent(conn: sqlite3.Connection) -> None:
    # Calling init_db a second time must not raise or wipe data.
    set_tag(conn, "conv-1", "proj-A")
    init_db(conn)
    assert get_tag(conn, "conv-1") == "proj-A"


def test_get_tag_returns_none_when_untagged(conn: sqlite3.Connection) -> None:
    assert get_tag(conn, "conv-never-tagged") is None


def test_set_tag_then_get_tag(conn: sqlite3.Connection) -> None:
    set_tag(conn, "conv-1", "proj-A")
    assert get_tag(conn, "conv-1") == "proj-A"


def test_set_tag_upserts_on_retag(conn: sqlite3.Connection) -> None:
    set_tag(conn, "conv-1", "proj-A")
    set_tag(conn, "conv-1", "proj-B")
    assert get_tag(conn, "conv-1") == "proj-B"
    # And only one row exists.
    rows = conn.execute("SELECT COUNT(*) FROM conversation_project").fetchone()
    assert rows[0] == 1


def test_set_tag_with_none_clears(conn: sqlite3.Connection) -> None:
    set_tag(conn, "conv-1", "proj-A")
    set_tag(conn, "conv-1", None)
    assert get_tag(conn, "conv-1") is None


def test_clearing_a_never_tagged_conversation_is_a_no_op(conn: sqlite3.Connection) -> None:
    # Should not raise.
    set_tag(conn, "conv-never-existed", None)
    assert get_tag(conn, "conv-never-existed") is None


def test_all_tags_returns_full_map(conn: sqlite3.Connection) -> None:
    set_tag(conn, "conv-1", "proj-A")
    set_tag(conn, "conv-2", "proj-A")
    set_tag(conn, "conv-3", "proj-B")
    assert all_tags(conn) == {
        "conv-1": "proj-A",
        "conv-2": "proj-A",
        "conv-3": "proj-B",
    }


def test_all_tags_empty_when_no_rows(conn: sqlite3.Connection) -> None:
    assert all_tags(conn) == {}


def test_tags_for_project_returns_only_matching(conn: sqlite3.Connection) -> None:
    set_tag(conn, "conv-1", "proj-A")
    set_tag(conn, "conv-2", "proj-A")
    set_tag(conn, "conv-3", "proj-B")
    assert sorted(tags_for_project(conn, "proj-A")) == ["conv-1", "conv-2"]
    assert tags_for_project(conn, "proj-B") == ["conv-3"]
    assert tags_for_project(conn, "proj-never-existed") == []


def test_tagged_at_is_iso_utc(conn: sqlite3.Connection) -> None:
    set_tag(conn, "conv-1", "proj-A")
    row = conn.execute(
        "SELECT tagged_at FROM conversation_project WHERE conversation_uuid = ?",
        ("conv-1",),
    ).fetchone()
    # ISO 8601 UTC ends with +00:00 from datetime.now(UTC).isoformat()
    assert row[0].endswith("+00:00")
