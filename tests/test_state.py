"""Unit tests for ``viewer.state`` — tag-state resolution + purge.

Pure SQLite + in-memory ``Export`` construction; no Flask app context needed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import DUAL_FORMAT_IDS, DUAL_FORMATS
from viewer.state import compute_tag_state, purge_orphan_tags
from viewer.tagging import all_tags, init_db, set_tag


@pytest.fixture(params=DUAL_FORMATS, ids=DUAL_FORMAT_IDS)
def export_path(request, fixtures_dir: Path) -> Path:
    """Dual-format module (v1.3.0 Session S): export-dependent tests in this
    file run against both container formats."""
    return fixtures_dir / request.param


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    yield c
    c.close()


# Synthetic UUIDs from build_fixtures.py — readable enough to follow.
_CONV_1 = "00000000-0000-0000-0000-0000000000c1"
_CONV_2 = "00000000-0000-0000-0000-0000000000d1"
_PROJ_A = "00000000-0000-0000-0000-0000000000a1"
_PROJ_B = "00000000-0000-0000-0000-0000000000b1"
_GHOST_CONV = "00000000-0000-0000-0000-0000000ghost"  # not in Export
_GHOST_PROJ = "00000000-0000-0000-0000-0000000gh0st"  # not in Export


def test_compute_tag_state_empty_table(conn, export) -> None:
    state = compute_tag_state(conn, export)
    assert state.live == {}
    assert state.orphan_conv == {}
    assert state.orphan_project == {}
    assert state.orphan_count == 0


def test_compute_tag_state_only_live(conn, export) -> None:
    set_tag(conn, _CONV_1, _PROJ_A)
    set_tag(conn, _CONV_2, _PROJ_B)
    state = compute_tag_state(conn, export)
    assert state.live == {_CONV_1: _PROJ_A, _CONV_2: _PROJ_B}
    assert state.orphan_conv == {}
    assert state.orphan_project == {}


def test_compute_tag_state_classifies_orphan_conv(conn, export) -> None:
    """A row whose conversation_uuid isn't in the current Export."""
    set_tag(conn, _GHOST_CONV, _PROJ_A)
    state = compute_tag_state(conn, export)
    assert state.orphan_conv == {_GHOST_CONV: _PROJ_A}
    assert state.live == {}
    assert state.orphan_project == {}
    assert state.orphan_count == 1


def test_compute_tag_state_classifies_orphan_project(conn, export) -> None:
    """A row whose project_uuid isn't in the current Export."""
    set_tag(conn, _CONV_1, _GHOST_PROJ)
    state = compute_tag_state(conn, export)
    assert state.orphan_project == {_CONV_1: _GHOST_PROJ}
    assert state.live == {}
    assert state.orphan_conv == {}


def test_compute_tag_state_mixed(conn, export) -> None:
    """All three categories present in one table."""
    set_tag(conn, _CONV_1, _PROJ_A)  # live
    set_tag(conn, _CONV_2, _GHOST_PROJ)  # orphan_project
    set_tag(conn, _GHOST_CONV, _PROJ_B)  # orphan_conv
    state = compute_tag_state(conn, export)
    assert state.live == {_CONV_1: _PROJ_A}
    assert state.orphan_project == {_CONV_2: _GHOST_PROJ}
    assert state.orphan_conv == {_GHOST_CONV: _PROJ_B}
    assert state.orphan_count == 2


def test_orphan_project_uuids_dedupes(conn, export) -> None:
    """If two convs are both tagged to the same orphan project, the
    distinct-uuid list collapses them."""
    set_tag(conn, _CONV_1, _GHOST_PROJ)
    set_tag(conn, _CONV_2, _GHOST_PROJ)
    state = compute_tag_state(conn, export)
    assert state.orphan_project_uuids == [_GHOST_PROJ]


def test_purge_orphan_tags_deletes_only_orphans(conn, export) -> None:
    set_tag(conn, _CONV_1, _PROJ_A)  # live — must survive
    set_tag(conn, _CONV_2, _GHOST_PROJ)  # orphan_project — gone
    set_tag(conn, _GHOST_CONV, _PROJ_B)  # orphan_conv — gone
    state = compute_tag_state(conn, export)

    deleted = purge_orphan_tags(conn, state)
    assert deleted == 2

    remaining = all_tags(conn)
    assert remaining == {_CONV_1: _PROJ_A}


def test_purge_orphan_tags_no_op_when_clean(conn, export) -> None:
    set_tag(conn, _CONV_1, _PROJ_A)
    state = compute_tag_state(conn, export)
    deleted = purge_orphan_tags(conn, state)
    assert deleted == 0
    assert all_tags(conn) == {_CONV_1: _PROJ_A}


def test_tag_state_is_frozen(conn, export) -> None:
    """``@dataclass(frozen=True, slots=True)`` — no accidental mutation."""
    state = compute_tag_state(conn, export)
    with pytest.raises((AttributeError, Exception)):
        state.live = {}  # type: ignore[misc]
