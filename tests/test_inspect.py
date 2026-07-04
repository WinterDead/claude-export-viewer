"""Inspect mode (v1.2 Session K): read-only, in-memory, no-trace.

Covers the brief's ten-item test list:

1.  Inspecting a valid zip swaps the view, shows the banner with the
    filename, and renders conversations/projects/memory from the inspected
    export.
2.  All mutating routes refuse during inspection, and ``viewer.sqlite`` is
    byte-identical across an inspect session that browses and searches.
3.  No-trace: the ``cache/`` directory snapshot is unchanged by inspection.
4.  No ``upload_history`` row is created by inspection.
5.  Search works against the inspected export; the primary FTS index is
    unchanged by it.
6.  "Return to my export" restores the primary view; tagging works again.
7.  A corrupt zip errors on the Inspect page; the current view is untouched.
8.  Tag dropdowns, tag filter, and mutating bulk actions are absent in
    inspect mode; the Upload page shows the disabled state.
9.  Banner text matches the authored copy on home, conversations, and help.
10. The help section ships and its authored copy is intact (the existing
    verify-negative tests in test_help.py keep guarding the page).

Plus parser-level coverage for :func:`viewer.parser.load_export_from_bytes`.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app import create_app
from viewer.parser import ExportFormatError, load_export, load_export_from_bytes
from viewer.routes import HELP_CONTENT_PATH

# Chat-authored copy (v1.2 Session K brief) — ships byte-exact.
INSPECT_FILENAME = "other_export.zip"
BANNER_TEXT = f"Inspecting {INSPECT_FILENAME} — read-only. Your own export and tags are untouched."
EXIT_LABEL = "Return to my export"

# Markers injected into the in-memory inspected fixture so tests can tell
# the inspected view from the primary synthetic fixture.
INSPECT_CONV_TITLE = "Inspected-only conversation"
INSPECT_PROJECT_NAME = "Inspected-only project"
INSPECT_MEMORY_MARKER = "zzyzx-inspect-memory-marker"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_factory(tmp_path: Path, fixtures_dir: Path):
    """Build an app whose cache paths (active zip, pickle, viewer.sqlite)
    all live under ``tmp_path/cache`` so the no-trace snapshots cover the
    complete on-disk state the app can write."""

    def _build():
        import app as app_module

        cache_dir = tmp_path / "cache"
        app_module.CACHE_DIR = cache_dir
        app_module.ACTIVE_ZIP = cache_dir / "active_export.zip"
        app_module.PARSE_CACHE = cache_dir / "parsed_export.pkl"

        flask_app = create_app(
            fixtures_dir / "synthetic_export.zip",
            db_path=cache_dir / "viewer.sqlite",
        )
        flask_app.config["ACTIVE_ZIP_PATH"] = app_module.ACTIVE_ZIP
        flask_app.config["PARSE_CACHE_PATH"] = app_module.PARSE_CACHE
        return flask_app

    return _build


@pytest.fixture
def inspect_zip_bytes(fixtures_dir: Path) -> bytes:
    """The synthetic fixture rebuilt in memory with distinguishing markers:
    a renamed conversation, a renamed project, and a unique token appended
    to the global memory. Never written to disk."""
    src = (fixtures_dir / "synthetic_export.zip").read_bytes()
    with zipfile.ZipFile(io.BytesIO(src)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}

    convs = json.loads(files["conversations.json"])
    convs[0]["name"] = INSPECT_CONV_TITLE
    files["conversations.json"] = json.dumps(convs).encode("utf-8")

    mems = json.loads(files["memories.json"])
    mems[0]["conversations_memory"] += "\n\n" + INSPECT_MEMORY_MARKER
    files["memories.json"] = json.dumps(mems).encode("utf-8")

    for name in sorted(files):
        if name.startswith("projects/") and name.endswith(".json"):
            proj = json.loads(files[name])
            proj["name"] = INSPECT_PROJECT_NAME
            files[name] = json.dumps(proj).encode("utf-8")
            break

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, blob in files.items():
            zf.writestr(name, blob)
    return out.getvalue()


def _csrf(client, page: str = "/inspect") -> str:
    body = client.get(page).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert m
    return m.group(1)


def _start_inspection(client, zip_bytes: bytes, filename: str = INSPECT_FILENAME) -> None:
    r = client.post(
        "/inspect",
        data={
            "csrf_token": _csrf(client),
            "export_zip": (io.BytesIO(zip_bytes), filename),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 302


def _exit_inspection(client) -> None:
    r = client.post("/inspect/exit", data={"csrf_token": _csrf(client)})
    assert r.status_code == 302


def _snapshot(root: Path) -> dict[str, bytes]:
    """Relative path → file bytes for every file under ``root``. Equality of
    two snapshots means no file was added, removed, or modified."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


BROWSE_PAGES = (
    "/",
    "/conversations",
    "/projects",
    "/memory",
    "/search?q=synthetic",
    "/upload",
    "/inspect",
    "/history",
    "/orphans",
    "/help",
)


# ---------------------------------------------------------------------------
# 1. Swap, banner, rendered content
# ---------------------------------------------------------------------------


def test_inspect_swaps_view_and_renders_inspected_content(app_factory, inspect_zip_bytes) -> None:
    client = app_factory().test_client()
    _start_inspection(client, inspect_zip_bytes)

    home = client.get("/").get_data(as_text=True)
    assert BANNER_TEXT in home
    assert INSPECT_FILENAME in home

    convs = client.get("/conversations").get_data(as_text=True)
    assert INSPECT_CONV_TITLE in convs

    projects = client.get("/projects").get_data(as_text=True)
    assert INSPECT_PROJECT_NAME in projects

    memory = client.get("/memory").get_data(as_text=True)
    assert INSPECT_MEMORY_MARKER in memory


def test_inspect_page_reachable_from_nav_and_shows_authored_intro(app_factory) -> None:
    client = app_factory().test_client()
    home = client.get("/").get_data(as_text=True)
    assert 'href="/inspect"' in home

    body = client.get("/inspect").get_data(as_text=True)
    assert "Inspect an export" in body
    normalised = re.sub(r"\s+", " ", body)
    # Authored intro copy, byte-exact modulo the template's line wrapping.
    assert (
        "Open a Claude data export temporarily, without touching your own data. "
        "The inspected export is read-only: no tagging, nothing saved, and nothing "
        "added to your upload history. When you're done — or when the app restarts — "
        "you're back to your own export. To replace your stored export instead, "
        "use Upload." in normalised
    )


# ---------------------------------------------------------------------------
# 2. Mutating routes refuse; tag database byte-identical
# ---------------------------------------------------------------------------


def test_mutating_routes_refuse_and_db_is_byte_identical(
    app_factory, inspect_zip_bytes, tmp_path: Path
) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    db_file = tmp_path / "cache" / "viewer.sqlite"

    _start_inspection(client, inspect_zip_bytes)
    before = db_file.read_bytes()

    token = _csrf(client)
    conv_uuid = flask_app.config["EXPORT"].conversations[0].uuid
    project_uuid = flask_app.config["EXPORT"].projects[0].uuid

    r = client.post(
        "/tag",
        data={
            "csrf_token": token,
            "conversation_uuid": conv_uuid,
            "project_uuid": project_uuid,
        },
    )
    assert r.status_code == 403

    r = client.post(
        "/tag/bulk",
        data={"csrf_token": token, "mode": "untag", "conversation_uuid": conv_uuid},
    )
    assert r.status_code == 403

    r = client.post("/orphans/purge", data={"csrf_token": token, "confirm": "yes"})
    assert r.status_code == 403

    r = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "export_zip": (io.BytesIO(inspect_zip_bytes), "replacement.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 403

    # Browse and search the inspected export, then confirm the tag/history/
    # FTS database is untouched down to the byte.
    for page in BROWSE_PAGES:
        assert client.get(page).status_code == 200, page
    assert client.get(f"/search?q={INSPECT_MEMORY_MARKER.split('-')[0]}").status_code == 200

    assert db_file.read_bytes() == before


# ---------------------------------------------------------------------------
# 3. No-trace: cache/ snapshot unchanged
# ---------------------------------------------------------------------------


def test_inspection_leaves_no_disk_trace(app_factory, inspect_zip_bytes, tmp_path: Path) -> None:
    client = app_factory().test_client()
    cache_dir = tmp_path / "cache"

    client.get("/")  # settle any first-request work before the snapshot
    before = _snapshot(cache_dir)
    assert "viewer.sqlite" in before  # snapshot covers the database

    _start_inspection(client, inspect_zip_bytes)
    for page in BROWSE_PAGES:
        client.get(page)
    client.get("/search?q=zzyzx")
    _exit_inspection(client)

    assert _snapshot(cache_dir) == before


# ---------------------------------------------------------------------------
# 4. No upload_history row
# ---------------------------------------------------------------------------


def test_inspection_records_no_history_row(app_factory, inspect_zip_bytes, tmp_path: Path) -> None:
    client = app_factory().test_client()
    db_file = tmp_path / "cache" / "viewer.sqlite"

    def history_count() -> int:
        with sqlite3.connect(db_file) as conn:
            return conn.execute("SELECT COUNT(*) FROM upload_history").fetchone()[0]

    assert history_count() == 0  # fixture startup loads never record
    _start_inspection(client, inspect_zip_bytes)
    client.get("/history")
    _exit_inspection(client)
    assert history_count() == 0


# ---------------------------------------------------------------------------
# 5. Search: inspected index serves, primary index untouched
# ---------------------------------------------------------------------------


def test_search_hits_inspected_export_and_primary_index_is_unchanged(
    app_factory, inspect_zip_bytes, tmp_path: Path
) -> None:
    client = app_factory().test_client()
    db_file = tmp_path / "cache" / "viewer.sqlite"

    def primary_index_state() -> tuple[int, int]:
        with sqlite3.connect(db_file) as conn:
            total = conn.execute("SELECT COUNT(*) FROM search").fetchone()[0]
            marker = conn.execute(
                "SELECT COUNT(*) FROM search WHERE search MATCH 'zzyzx'"
            ).fetchone()[0]
            return total, marker

    before = primary_index_state()
    assert before[1] == 0  # marker absent from the primary index

    _start_inspection(client, inspect_zip_bytes)

    # Body hit (global memory) renders a highlighted snippet.
    body = client.get("/search?q=zzyzx").get_data(as_text=True)
    assert "<mark>" in body
    # Title hit groups under the inspected conversation.
    body = client.get("/search?q=%22Inspected-only%22").get_data(as_text=True)
    assert INSPECT_CONV_TITLE in body

    assert primary_index_state() == before

    _exit_inspection(client)
    body = client.get("/search?q=zzyzx").get_data(as_text=True)
    assert "<mark>" not in body


# ---------------------------------------------------------------------------
# 6. Return to my export
# ---------------------------------------------------------------------------


def test_return_restores_primary_view_and_tagging(app_factory, inspect_zip_bytes) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    _start_inspection(client, inspect_zip_bytes)
    _exit_inspection(client)

    home = client.get("/").get_data(as_text=True)
    assert BANNER_TEXT not in home
    assert EXIT_LABEL not in home

    convs = client.get("/conversations").get_data(as_text=True)
    assert INSPECT_CONV_TITLE not in convs
    assert "Synthetic minimal conversation" in convs

    # Tagging works again end-to-end.
    conv_uuid = flask_app.config["EXPORT"].conversations[0].uuid
    project_uuid = flask_app.config["EXPORT"].projects[0].uuid
    r = client.post(
        "/tag",
        data={
            "csrf_token": _csrf(client),
            "conversation_uuid": conv_uuid,
            "project_uuid": project_uuid,
        },
    )
    assert r.status_code == 302
    with flask_app.app_context():
        from viewer.db import get_db
        from viewer.tagging import get_tag

        assert get_tag(get_db(), conv_uuid) == project_uuid


# ---------------------------------------------------------------------------
# 7. Corrupt zip errors on the Inspect page; current view untouched
# ---------------------------------------------------------------------------


def test_corrupt_zip_errors_and_leaves_view_untouched(
    app_factory, inspect_zip_bytes, fixtures_dir: Path
) -> None:
    client = app_factory().test_client()
    corrupt = (fixtures_dir / "corrupt_export.zip").read_bytes()

    # From the primary view: error surfaces, no inspection starts.
    r = client.post(
        "/inspect",
        data={
            "csrf_token": _csrf(client),
            "export_zip": (io.BytesIO(corrupt), "corrupt.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert "parse archive" in r.get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)
    assert EXIT_LABEL not in home
    assert "Synthetic minimal conversation" in client.get("/conversations").get_data(as_text=True)

    # From an inspecting view: the previously inspected export stays loaded.
    _start_inspection(client, inspect_zip_bytes)
    r = client.post(
        "/inspect",
        data={
            "csrf_token": _csrf(client),
            "export_zip": (io.BytesIO(corrupt), "corrupt.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    home = client.get("/").get_data(as_text=True)
    assert BANNER_TEXT in home  # still inspecting the earlier file


# ---------------------------------------------------------------------------
# 8. Tag UI absent; Upload page disabled
# ---------------------------------------------------------------------------


def test_tag_ui_absent_and_upload_disabled_in_inspect_mode(app_factory, inspect_zip_bytes) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    _start_inspection(client, inspect_zip_bytes)

    convs = client.get("/conversations").get_data(as_text=True)
    assert "tag-form" not in convs  # per-row dropdowns
    assert "tag-filter" not in convs  # filter control
    assert "bulk-form" not in convs and "bulk-bar" not in convs  # bulk action bar
    assert "conv-checkbox" not in convs  # row selection
    assert "bulk_tag.js" not in convs  # controller not even loaded

    from viewer.inspect import get_inspection

    inspected_conv = get_inspection(flask_app).export.conversations[0]
    detail = client.get(f"/conversations/{inspected_conv.uuid}").get_data(as_text=True)
    assert "tag-form" not in detail

    projects = client.get("/projects").get_data(as_text=True)
    assert "tagged conversation" not in projects

    upload = client.get("/upload").get_data(as_text=True)
    assert "<fieldset" in upload and "disabled" in upload.split("<fieldset", 1)[1].split(">", 1)[0]
    assert "Uploading is disabled while you" in upload

    orphans = client.get("/orphans").get_data(as_text=True)
    assert "orphan-purge-form" not in orphans


# ---------------------------------------------------------------------------
# 9. Banner copy, byte-exact, on several pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", ("/", "/conversations", "/help"))
def test_banner_matches_authored_copy_on_page(app_factory, inspect_zip_bytes, page: str) -> None:
    client = app_factory().test_client()
    _start_inspection(client, inspect_zip_bytes)
    body = client.get(page).get_data(as_text=True)
    assert BANNER_TEXT in body
    assert f">{EXIT_LABEL}</button>" in body


# ---------------------------------------------------------------------------
# 10. Help section
# ---------------------------------------------------------------------------

# The authored help copy (v1.2 Session K brief), with two mechanical
# adaptations: the sanctioned link markup on "Inspect page", and the
# brief's soft line wraps reflowed to help.md's one-line-per-paragraph
# convention (the renderer's nl2br would otherwise force breaks no other
# section has). Wording and punctuation are byte-exact.
EXPECTED_HELP_SECTION = (
    "## Inspecting another export\n"
    "\n"
    "Sometimes you want to look inside an export without making it yours — a second "
    "account's archive, a colleague's export, or a test file. The "
    "[Inspect page](/inspect) opens an export temporarily and read-only: no tagging, "
    "nothing saved to disk, nothing recorded in upload history, and your own export "
    "and tags stay exactly as they were. A banner reminds you which file you're "
    "inspecting; the **Return to my export** button in the banner (or simply "
    "restarting the app) puts everything back. To replace your stored export "
    "instead, use Upload."
)


def test_help_source_carries_authored_section_after_loading_section() -> None:
    text = HELP_CONTENT_PATH.read_text(encoding="utf-8")
    assert EXPECTED_HELP_SECTION in text
    loading = text.index("## Loading an export into the viewer")
    inspecting = text.index("## Inspecting another export")
    home = text.index("## The home page")
    assert loading < inspecting < home


def test_help_page_renders_inspect_section_with_link(app_factory) -> None:
    client = app_factory().test_client()
    body = client.get("/help").get_data(as_text=True)
    assert 'id="inspecting-another-export"' in body
    assert 'href="/inspect"' in body
    normalised = re.sub(r"\s+", " ", body)
    assert (
        "Sometimes you want to look inside an export without making it yours — "
        "a second account's archive, a colleague's export, or a test file." in normalised
    )
    assert "your own export and tags stay exactly as they were." in normalised


# ---------------------------------------------------------------------------
# Parser: load_export_from_bytes
# ---------------------------------------------------------------------------


def test_load_export_from_bytes_matches_path_load(fixtures_dir: Path) -> None:
    data = (fixtures_dir / "synthetic_export.zip").read_bytes()
    from_bytes = load_export_from_bytes(data, name="synthetic_export.zip")
    from_path = load_export(fixtures_dir / "synthetic_export.zip")

    assert from_bytes.source_paths == ["synthetic_export.zip"]
    assert from_bytes.user.uuid == from_path.user.uuid
    assert {c.uuid for c in from_bytes.conversations} == {c.uuid for c in from_path.conversations}
    assert {p.uuid for p in from_bytes.projects} == {p.uuid for p in from_path.projects}
    assert from_bytes.global_memory.content == from_path.global_memory.content


def test_load_export_from_bytes_rejects_non_zip_bytes() -> None:
    with pytest.raises(ExportFormatError, match="not a valid zip"):
        load_export_from_bytes(b"definitely not a zip archive", name="junk.zip")
