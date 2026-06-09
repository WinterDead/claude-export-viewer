"""Tests for the "No Project" sentinel tag (v1.1 Session B).

The sentinel (``viewer.tagging.NO_PROJECT_SENTINEL``) records "reviewed, and
confirmed to belong to no project" — distinct from untagged ("not yet
reviewed"). Covers every surface from the Session B brief: both tagging
dropdowns, bulk modes, the ``?tag=no-project`` filter, sentinel absence from
``/projects`` and the home stats, the orphan-semantics split, the
project-detail redirect, and persistence across a re-import.
"""

from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from app import create_app
from viewer.parser import load_export
from viewer.state import compute_tag_state, purge_orphan_tags
from viewer.tagging import NO_PROJECT_LABEL, NO_PROJECT_SENTINEL, all_tags, init_db, set_tag

# Synthetic UUIDs — match the happy-path fixture.
PROJ_A = "00000000-0000-0000-0000-0000000000a1"
PROJ_B = "00000000-0000-0000-0000-0000000000b1"
CONV_1 = "00000000-0000-0000-0000-0000000000c1"
CONV_2 = "00000000-0000-0000-0000-0000000000d1"
CONV_3 = "00000000-0000-0000-0000-0000000000e1"
GHOST_CONV = "00000000-0000-0000-0000-0000000ghost"  # not in any fixture


@pytest.fixture
def app(tmp_path: Path, fixtures_dir: Path):
    return create_app(
        fixtures_dir / "synthetic_export.zip",
        db_path=tmp_path / "viewer.sqlite",
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def synthetic_export(fixtures_dir: Path):
    return load_export(fixtures_dir / "synthetic_export.zip")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_db(c)
    yield c
    c.close()


def _seed(app, **rows):
    """Open an app context and stuff rows into the tag table."""
    with app.app_context():
        from viewer.db import get_db

        db = get_db()
        for cuid, puid in rows.items():
            set_tag(db, cuid, puid)


def _tags(app) -> dict[str, str]:
    with app.app_context():
        from viewer.db import get_db

        return all_tags(get_db())


def _csrf(client) -> str:
    body = client.get("/upload").get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert m, "no csrf_token in rendered form"
    return m.group(1)


def _selected_option(page_body: str, conv_uuid: str) -> str | None:
    """Return the value of the selected ``project_uuid`` option in the tag
    dropdown belonging to ``conv_uuid``. Attribute order varies (the sentinel
    and orphan options carry a class), so match any attributes before
    ``selected``."""
    row = re.search(r'value="' + re.escape(conv_uuid) + r'"[\s\S]*?</select>', page_body)
    assert row, f"no tag form for {conv_uuid}"
    selected = re.search(r'<option value="([^"]*)"[^>]*?\bselected\b', row.group(0))
    if not selected:
        return None
    return selected.group(1) or None


def _post_tag(client, conv_uuid: str, project_uuid: str, next_url: str = ""):
    return client.post(
        "/tag",
        data={
            "csrf_token": _csrf(client),
            "conversation_uuid": conv_uuid,
            "project_uuid": project_uuid,
            "next": next_url,
        },
    )


def _post_bulk(client, mode: str, *, conv_uuids: list[str], project_uuid: str = ""):
    pairs = [
        ("csrf_token", _csrf(client)),
        ("mode", mode),
        ("project_uuid", project_uuid),
    ]
    pairs.extend(("conversation_uuid", u) for u in conv_uuids)
    return client.post("/tag/bulk", data=MultiDict(pairs))


def _listed_conv_uuids(client, url: str) -> set[str]:
    """The conversation UUIDs rendered on a conversations-list page, read
    from the bulk checkboxes (one per row)."""
    body = client.get(url).get_data(as_text=True)
    return set(re.findall(r'name="conversation_uuid"\s+value="([^"]+)"', body))


# ---------------------------------------------------------------------------
# Tagging via the dropdowns (single-conversation endpoint)
# ---------------------------------------------------------------------------


def test_tag_to_no_project_via_conversations_list_dropdown(client) -> None:
    r = _post_tag(client, CONV_1, NO_PROJECT_SENTINEL, next_url="/conversations")
    assert r.status_code == 302
    body = client.get("/conversations").get_data(as_text=True)
    assert _selected_option(body, CONV_1) == NO_PROJECT_SENTINEL


def test_tag_to_no_project_via_detail_page_dropdown(client) -> None:
    r = _post_tag(client, CONV_1, NO_PROJECT_SENTINEL, next_url=f"/conversations/{CONV_1}")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/conversations/{CONV_1}")
    body = client.get(f"/conversations/{CONV_1}").get_data(as_text=True)
    assert _selected_option(body, CONV_1) == NO_PROJECT_SENTINEL
    # The sentinel is a normal tag, never the orphan affordance.
    assert "(Orphan:" not in body


def test_no_project_listed_first_ahead_of_real_projects(client) -> None:
    """The sentinel option precedes every real-project option and is distinct
    from the clear option (different value, different label)."""
    body = client.get(f"/conversations/{CONV_1}").get_data(as_text=True)
    sentinel_pos = body.find(f'value="{NO_PROJECT_SENTINEL}"')
    assert sentinel_pos != -1
    for proj in (PROJ_A, PROJ_B):
        proj_pos = body.find(f'<option value="{proj}"')
        assert proj_pos != -1
        assert sentinel_pos < proj_pos
    assert "(Untagged)" in body  # the clear option still reads as untag


def test_untag_returns_no_project_row_to_untagged(client, app) -> None:
    """Untagging and tagging-to-No-Project are different actions: clearing a
    sentinel row puts the conversation back in the untagged (triage) pool."""
    _post_tag(client, CONV_1, NO_PROJECT_SENTINEL)
    _post_tag(client, CONV_1, "")  # the "(Untagged)" option submits empty
    assert CONV_1 not in _tags(app)
    assert CONV_1 in _listed_conv_uuids(client, "/conversations?tag=untagged")


# ---------------------------------------------------------------------------
# Bulk tagging
# ---------------------------------------------------------------------------


def test_bulk_tag_selection_to_no_project(client, app) -> None:
    r = _post_bulk(client, "tag", conv_uuids=[CONV_1, CONV_2], project_uuid=NO_PROJECT_SENTINEL)
    assert r.status_code == 302
    tags = _tags(app)
    assert tags[CONV_1] == NO_PROJECT_SENTINEL
    assert tags[CONV_2] == NO_PROJECT_SENTINEL


def test_bulk_retag_tagged_rows_to_no_project(client, app) -> None:
    _post_bulk(client, "tag", conv_uuids=[CONV_1], project_uuid=PROJ_A)
    r = _post_bulk(client, "retag", conv_uuids=[CONV_1, CONV_2], project_uuid=NO_PROJECT_SENTINEL)
    assert r.status_code == 302
    tags = _tags(app)
    assert tags[CONV_1] == NO_PROJECT_SENTINEL
    assert tags[CONV_2] == NO_PROJECT_SENTINEL


def test_bulk_tag_mode_skips_rows_already_marked_no_project(client, app) -> None:
    """Sentinel rows count as tagged for tag mode's skip rule — the existing
    mode behaviours are unchanged, the sentinel is just another target."""
    _post_tag(client, CONV_1, NO_PROJECT_SENTINEL)
    _post_bulk(client, "tag", conv_uuids=[CONV_1, CONV_2], project_uuid=PROJ_A)
    tags = _tags(app)
    assert tags[CONV_1] == NO_PROJECT_SENTINEL  # skipped, not overwritten
    assert tags[CONV_2] == PROJ_A


def test_bulk_untag_clears_no_project_rows(client, app) -> None:
    _post_bulk(client, "tag", conv_uuids=[CONV_1, CONV_2], project_uuid=NO_PROJECT_SENTINEL)
    _post_bulk(client, "untag", conv_uuids=[CONV_1])
    tags = _tags(app)
    assert CONV_1 not in tags
    assert tags[CONV_2] == NO_PROJECT_SENTINEL


def test_bulk_picker_offers_no_project(client) -> None:
    body = client.get("/conversations").get_data(as_text=True)
    picker = re.search(
        r'<select name="project_uuid" class="bulk-project-picker"[\s\S]*?</select>', body
    )
    assert picker
    assert f'value="{NO_PROJECT_SENTINEL}"' in picker.group(0)
    assert NO_PROJECT_LABEL in picker.group(0)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_filters(app):
    """CONV_1 → sentinel, CONV_2 → real project, CONV_3 untagged."""
    _seed(app, **{CONV_1: NO_PROJECT_SENTINEL, CONV_2: PROJ_A})


def test_filter_no_project_shows_only_sentinel_rows(client, seeded_filters) -> None:
    listed = _listed_conv_uuids(client, "/conversations?tag=no-project")
    assert CONV_1 in listed
    assert CONV_2 not in listed
    assert CONV_3 not in listed


def test_filter_untagged_excludes_sentinel_rows(client, seeded_filters) -> None:
    listed = _listed_conv_uuids(client, "/conversations?tag=untagged")
    assert CONV_1 not in listed
    assert CONV_2 not in listed
    assert CONV_3 in listed


def test_filter_real_project_excludes_sentinel_rows(client, seeded_filters) -> None:
    listed = _listed_conv_uuids(client, f"/conversations?tag={PROJ_A}")
    assert CONV_2 in listed
    assert CONV_1 not in listed


def test_filter_select_offers_no_project_slug(client) -> None:
    body = client.get("/conversations").get_data(as_text=True)
    assert 'value="no-project"' in body
    assert NO_PROJECT_LABEL in body


def test_filter_no_project_empty_state(client) -> None:
    body = client.get("/conversations?tag=no-project").get_data(as_text=True)
    assert f"No conversations marked {NO_PROJECT_LABEL} yet." in body


# ---------------------------------------------------------------------------
# Surfaces the sentinel must never reach
# ---------------------------------------------------------------------------


def test_projects_page_never_lists_sentinel(client, app) -> None:
    _seed(app, **{CONV_1: NO_PROJECT_SENTINEL})
    body = client.get("/projects").get_data(as_text=True)
    assert NO_PROJECT_SENTINEL not in body
    assert NO_PROJECT_LABEL not in body


def test_home_project_count_unchanged_by_sentinel_rows(client, app) -> None:
    def project_stat() -> str:
        body = client.get("/").get_data(as_text=True)
        m = re.search(r"<dt>Projects</dt><dd>(\d+)</dd>", body)
        assert m, "no Projects stat on the home page"
        return m.group(1)

    before = project_stat()
    _seed(app, **{CONV_1: NO_PROJECT_SENTINEL, CONV_2: NO_PROJECT_SENTINEL})
    assert project_stat() == before


def test_sentinel_project_detail_url_redirects_to_filtered_list(client) -> None:
    r = client.get(f"/projects/{NO_PROJECT_SENTINEL}")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/conversations?tag=no-project")


# ---------------------------------------------------------------------------
# Orphan semantics — the B4 split
# ---------------------------------------------------------------------------


def test_sentinel_on_live_conversation_is_bucket_live(conn, synthetic_export) -> None:
    set_tag(conn, CONV_1, NO_PROJECT_SENTINEL)
    state = compute_tag_state(conn, synthetic_export)
    assert state.live == {CONV_1: NO_PROJECT_SENTINEL}
    assert state.orphan_conv == {}
    assert state.orphan_project == {}
    assert state.orphan_count == 0


def test_sentinel_on_missing_conversation_is_orphan_conv(conn, synthetic_export) -> None:
    """Orphan-conversation detection still applies — the row is unreachable."""
    set_tag(conn, GHOST_CONV, NO_PROJECT_SENTINEL)
    state = compute_tag_state(conn, synthetic_export)
    assert state.orphan_conv == {GHOST_CONV: NO_PROJECT_SENTINEL}
    assert state.orphan_project == {}


def test_purge_clears_sentinel_orphan_conv_but_not_live_sentinel(conn, synthetic_export) -> None:
    set_tag(conn, CONV_1, NO_PROJECT_SENTINEL)  # live — must survive
    set_tag(conn, GHOST_CONV, NO_PROJECT_SENTINEL)  # orphan-conv — gone
    state = compute_tag_state(conn, synthetic_export)
    assert purge_orphan_tags(conn, state) == 1
    assert all_tags(conn) == {CONV_1: NO_PROJECT_SENTINEL}


def test_no_orphan_banner_for_live_sentinel_rows(client, app) -> None:
    _seed(app, **{CONV_1: NO_PROJECT_SENTINEL})
    body = client.get("/").get_data(as_text=True)
    assert "orphan-banner" not in body
    body = client.get("/orphans").get_data(as_text=True)
    assert "No orphan tags" in body


def test_orphan_conv_sentinel_row_has_no_missing_project_badge(client, app) -> None:
    _seed(app, **{GHOST_CONV: NO_PROJECT_SENTINEL})
    body = client.get("/orphans").get_data(as_text=True)
    assert GHOST_CONV in body
    assert NO_PROJECT_LABEL in body  # rendered as the readable label
    assert "project also missing" not in body


def test_purge_route_clears_sentinel_orphan_conv_row(client, app) -> None:
    _seed(app, **{GHOST_CONV: NO_PROJECT_SENTINEL, CONV_1: NO_PROJECT_SENTINEL})
    r = client.post("/orphans/purge", data={"csrf_token": _csrf(client), "confirm": "yes"})
    assert r.status_code == 302
    assert _tags(app) == {CONV_1: NO_PROJECT_SENTINEL}


# ---------------------------------------------------------------------------
# Persistence across a re-import
# ---------------------------------------------------------------------------


def test_sentinel_tag_survives_reimport(tmp_path: Path, fixtures_dir: Path) -> None:
    """Upload a new archive containing the same conversations: the sentinel
    row persists and still resolves as live — the triage workflow's core
    property (reviewed-as-No-Project status survives while new conversations
    arrive untagged)."""
    import app as app_module

    app_module.CACHE_DIR = tmp_path / "cache"
    app_module.ACTIVE_ZIP = tmp_path / "cache" / "active_export.zip"
    app_module.PARSE_CACHE = tmp_path / "cache" / "parsed_export.pkl"

    flask_app = create_app(
        fixtures_dir / "synthetic_export.zip",
        db_path=tmp_path / "viewer.sqlite",
    )
    flask_app.config["ACTIVE_ZIP_PATH"] = app_module.ACTIVE_ZIP
    flask_app.config["PARSE_CACHE_PATH"] = app_module.PARSE_CACHE
    client = flask_app.test_client()

    _post_tag(client, CONV_1, NO_PROJECT_SENTINEL)

    # Re-import: same conversations plus an extra orphan memory.
    new_zip = (fixtures_dir / "bad_dangling_memory.zip").read_bytes()
    r = client.post(
        "/upload",
        data={
            "csrf_token": _csrf(client),
            "export_zip": (io.BytesIO(new_zip), "reimport.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 302

    assert _tags(flask_app)[CONV_1] == NO_PROJECT_SENTINEL
    assert CONV_1 in _listed_conv_uuids(client, "/conversations?tag=no-project")
    # Still live, not orphaned, after the swap.
    body = client.get("/").get_data(as_text=True)
    assert "orphan-banner" not in body
