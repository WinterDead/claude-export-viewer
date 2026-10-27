"""Multi-file upload and inspect sets (v1.3.0 Session S Phase 3).

Covers the design record's §4 (multi-file picker, sniff-and-slot,
duplicate-category last-wins, per-file cap knob, degraded-view banner with
the Appendix C1 copy) and §5 (set storage under ``cache/active_export/``,
set fingerprint, history summaries, startup pickup and dedup).

Every upload and inspection here goes through the form **as the template
actually renders it**: the field name, multiplicity, and accepted types are
scraped from the page, never hand-built (the Session O meta-gap rule).
"""

from __future__ import annotations

import io
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import create_app
from tests.conftest import SYNTHETIC_EXPORT, scrape_csrf
from viewer import routes as routes_module
from viewer.cache import set_fingerprint, zip_hash
from viewer.parser import KNOWN_CATEGORIES
from viewer.routes import _read_upload_cap_mb, summarise_set

# ---------------------------------------------------------------------------
# Real-rendered-form scraping
# ---------------------------------------------------------------------------


class _FileForm(HTMLParser):
    """The page's multipart form as rendered: action, method, enctype, the
    file input's attributes, and the CSRF field name."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.method: str | None = None
        self.enctype: str | None = None
        self.file_input: dict | None = None
        self.csrf_name: str | None = None
        self._in_form = False

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = dict(attrs_list)
        if tag == "form" and attrs.get("enctype") == "multipart/form-data":
            self._in_form = True
            self.action = attrs.get("action")
            self.method = (attrs.get("method") or "get").lower()
            self.enctype = attrs.get("enctype")
        elif tag == "input" and self._in_form:
            if attrs.get("type") == "file":
                self.file_input = attrs
            elif attrs.get("type") == "hidden" and "csrf" in (attrs.get("name") or ""):
                self.csrf_name = attrs["name"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False


def _scrape_file_form(client, page: str) -> _FileForm:
    body = client.get(page).get_data(as_text=True)
    form = _FileForm()
    form.feed(body)
    assert form.action, f"no multipart form on {page}"
    assert form.method == "post"
    assert form.file_input is not None
    return form


def _post_files(client, page: str, files: list[tuple[str, bytes]]):
    """Submit ``files`` through the page's real rendered form."""
    form = _scrape_file_form(client, page)
    name = form.file_input["name"]
    data: dict = {form.csrf_name: scrape_csrf(client, page)}
    data[name] = [(io.BytesIO(blob), fname) for fname, blob in files]
    return client.post(form.action, data=data, content_type="multipart/form-data")


@pytest.fixture
def current_files(fixtures_dir: Path) -> dict[str, Path]:
    """The current-format set's files, keyed by a short handle."""
    current = fixtures_dir / "current"
    manifest = next(p for p in current.iterdir() if p.suffix == ".json")
    return {
        "conversations": current / "conversations-000.zip",
        "projects": current / "projects-000.zip",
        "memories": current / "memories-000.zip",
        "light_metadata": current / "light_metadata-000.zip",
        # The three later categories (v1.3.1) — the full eight-file delivery.
        "frames": current / "frames-000.zip",
        "artifact_data": current / "artifact_data-000.zip",
        "claude_code_cloud": current / "claude_code_cloud-000.zip",
        "manifest": manifest,
    }


def _history_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as c:
        return c.execute(
            "SELECT source, filename, zip_sha256 FROM upload_history ORDER BY id"
        ).fetchall()


# ---------------------------------------------------------------------------
# The rendered form itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["/upload", "/inspect"])
def test_form_renders_multi_file_input(client, page: str) -> None:
    form = _scrape_file_form(client, page)
    assert form.file_input["name"] == "export_files"
    assert "multiple" in form.file_input
    accept = form.file_input.get("accept", "")
    assert ".zip" in accept and ".json" in accept


# ---------------------------------------------------------------------------
# Upload: full set and subsets (record §4/§5)
# ---------------------------------------------------------------------------


def test_full_set_upload_stores_files_as_received(app_factory, current_files) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    supplied = [(p.name, p.read_bytes()) for p in current_files.values()]

    r = _post_files(client, "/upload", supplied)
    assert r.status_code == 302

    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    stored = {p.name: p.read_bytes() for p in active_set.iterdir()}
    assert stored == dict(supplied)
    # The pre-set single-zip location is not used by the set flow.
    assert not flask_app.config["ACTIVE_ZIP_PATH"].exists()

    export = flask_app.config["EXPORT"]
    assert len(export.conversations) == 4
    assert export.user is not None
    # All seven categories recognised and kept (the v1.3.1 regression fix:
    # v1.3.0 refused this exact upload).
    assert export.categories == KNOWN_CATEGORIES

    # No degraded banner for a complete set.
    home = client.get("/").get_data(as_text=True)
    assert "export categories" not in home


def test_full_set_upload_records_summary_row_with_fingerprint(
    app_factory, current_files, tmp_path: Path
) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    supplied = [(p.name, p.read_bytes()) for p in current_files.values()]
    assert _post_files(client, "/upload", supplied).status_code == 302

    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    assert len(rows) == 1
    source, filename, fingerprint = rows[0]
    assert source == "upload"
    assert filename == (
        "8 files: conversations, projects, memories, light_metadata, frames, artifact_data, "
        "claude_code_cloud, manifest"
    )
    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    assert fingerprint == set_fingerprint(sorted(active_set.iterdir()))


def test_subset_upload_shows_degraded_banner_with_c1_copy(app_factory, current_files) -> None:
    client = app_factory().test_client()
    p = current_files["conversations"]
    assert _post_files(client, "/upload", [(p.name, p.read_bytes())]).status_code == 302

    home = client.get("/").get_data(as_text=True)
    # Appendix C1, byte-exact.
    assert "Loaded 1 of 4 export categories." in home
    assert "No projects file — project pages and project names are unavailable." in home
    assert "No memories file — the Memory page is unavailable." in home
    assert "No account file — account details and login history are unavailable." in home
    # The supplied category's line is absent.
    assert "No conversations file" not in home
    # No manifest, no expected-vs-supplied line.
    assert "The manifest lists" not in home


def test_manifest_mismatch_line_on_banner(app_factory, current_files) -> None:
    client = app_factory().test_client()
    supplied = [
        (current_files["manifest"].name, current_files["manifest"].read_bytes()),
        (current_files["conversations"].name, current_files["conversations"].read_bytes()),
    ]
    assert _post_files(client, "/upload", supplied).status_code == 302

    home = client.get("/").get_data(as_text=True)
    # A7: with a manifest, the lead counts against its seven categories.
    assert "Loaded 1 of 7 export categories." in home
    assert "The manifest lists 7 files; 1 were supplied." in home


def test_manifest_alone_is_rejected(app_factory, current_files) -> None:
    client = app_factory().test_client()
    p = current_files["manifest"]
    r = _post_files(client, "/upload", [(p.name, p.read_bytes())])
    assert r.status_code == 400
    assert "manifest alone" in r.get_data(as_text=True)


def test_duplicate_category_last_file_wins_with_warning(
    app_factory, current_files, fixtures_dir: Path, caplog
) -> None:
    """Record §4: two files of one category in a submission — the last wins."""
    flask_app = app_factory()
    client = flask_app.test_client()
    first = fixtures_dir / "current_two_part" / "conversations-000.zip"
    second = current_files["conversations"]

    import logging

    with caplog.at_level(logging.WARNING, logger="viewer.routes"):
        r = _post_files(
            client,
            "/upload",
            [("first-conversations.zip", first.read_bytes()), (second.name, second.read_bytes())],
        )
    assert r.status_code == 302
    assert any("duplicate conversations file" in rec.getMessage() for rec in caplog.records)

    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    stored = {p.name for p in active_set.iterdir()}
    assert stored == {second.name}
    assert len(flask_app.config["EXPORT"].conversations) == 4  # the full part, not part 0


def test_mixed_containers_refused_with_c11_message(app_factory, fixtures_dir: Path) -> None:
    """Record §2 (Session T amendment): a legacy archive plus current-format
    category files in one submission is refused by design; both routes
    render the Appendix C11 copy and the loaded export is untouched."""
    import html as html_mod

    from viewer.parser import MIXED_CONTAINER_MESSAGE

    flask_app = app_factory()
    client = flask_app.test_client()
    legacy = (fixtures_dir / SYNTHETIC_EXPORT).read_bytes()
    category = (fixtures_dir / "current" / "conversations-000.zip").read_bytes()
    before = flask_app.config["EXPORT"]

    for page in ("/upload", "/inspect"):
        r = _post_files(client, page, [("legacy.zip", legacy), ("conversations-000.zip", category)])
        assert r.status_code == 400, page
        assert MIXED_CONTAINER_MESSAGE in html_mod.unescape(r.get_data(as_text=True)), page
        assert flask_app.config["EXPORT"] is before, page


def test_two_parts_merge_in_suffix_order_supplied_out_of_order(
    app_factory, fixtures_dir: Path, tmp_path
) -> None:
    """Record §4 (Session T amendment): same-category files with distinct
    ``-NNN`` suffixes are parts — deliberately supplied out of order, they
    merge in part order (part 0's copy of the duplicated conversation wins)
    and the history row carries the part multiplicity (copy C3)."""
    flask_app = app_factory()
    client = flask_app.test_client()
    two_part = fixtures_dir / "current_two_part"
    p0 = (two_part / "conversations-000.zip").read_bytes()
    p1 = (two_part / "conversations-001.zip").read_bytes()

    r = _post_files(
        client, "/upload", [("conversations-001.zip", p1), ("conversations-000.zip", p0)]
    )
    assert r.status_code == 302

    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    assert {p.name for p in active_set.iterdir()} == {
        "conversations-000.zip",
        "conversations-001.zip",
    }
    export = flask_app.config["EXPORT"]
    assert len(export.conversations) == 4
    minimal = next(c for c in export.conversations if c.uuid.endswith("c1"))
    assert minimal.title == "Synthetic minimal conversation"  # part 0's copy won

    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    assert rows[-1][1] == "2 files: conversations ×2"


def test_two_parts_merge_in_manifest_order(app_factory, fixtures_dir: Path, tmp_path) -> None:
    """Record §4 (Session T amendment): the manifest's part numbers outrank
    filename suffixes — with suffixes deliberately contradicting the
    manifest, the manifest's order decides which duplicate copy wins."""
    import json

    flask_app = app_factory()
    client = flask_app.test_client()
    two_part = fixtures_dir / "current_two_part"
    part0 = (two_part / "conversations-000.zip").read_bytes()
    part1 = (two_part / "conversations-001.zip").read_bytes()
    # The names are swapped relative to their content, so suffix order would
    # merge part 1 first; the manifest says otherwise and must win.
    manifest = json.dumps(
        {
            "data_files": [
                {"category": "conversations", "part": 0, "filename": "conversations-001.zip"},
                {"category": "conversations", "part": 1, "filename": "conversations-000.zip"},
            ]
        }
    ).encode("utf-8")

    r = _post_files(
        client,
        "/upload",
        [
            ("conversations-000.zip", part1),
            ("conversations-001.zip", part0),
            ("manifest.json", manifest),
        ],
    )
    assert r.status_code == 302
    export = flask_app.config["EXPORT"]
    assert len(export.conversations) == 4
    minimal = next(c for c in export.conversations if c.uuid.endswith("c1"))
    assert minimal.title == "Synthetic minimal conversation"  # manifest part 0 won

    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    assert rows[-1][1] == "3 files: conversations ×2, manifest"


def test_inspect_two_parts_shows_multiplicity_banner(app_factory, fixtures_dir: Path) -> None:
    """The inspect banner's set summary carries the ×N part multiplier
    (copy C3); parts group through the same rules as Upload."""
    flask_app = app_factory()
    client = flask_app.test_client()
    two_part = fixtures_dir / "current_two_part"
    files = [
        ("conversations-001.zip", (two_part / "conversations-001.zip").read_bytes()),
        ("conversations-000.zip", (two_part / "conversations-000.zip").read_bytes()),
    ]
    r = _post_files(client, "/inspect", files)
    assert r.status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert "Inspecting 2 files: conversations ×2 — read-only." in body


def test_legacy_zip_upload_is_a_set_of_one(app_factory, fixtures_dir: Path, tmp_path) -> None:
    """Continuity (record §5): a legacy archive keeps its filename in the
    history row and its plain SHA-256 as the fingerprint."""
    flask_app = app_factory()
    client = flask_app.test_client()
    legacy = fixtures_dir / "bad_dangling_memory.zip"
    assert (
        _post_files(client, "/upload", [("my-export.zip", legacy.read_bytes())]).status_code == 302
    )

    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    assert [p.name for p in active_set.iterdir()] == ["my-export.zip"]

    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    source, filename, fingerprint = rows[0]
    assert filename == "my-export.zip"
    assert fingerprint == zip_hash(legacy)
    # And no degraded banner — a legacy archive carries all four categories.
    assert "export categories" not in client.get("/").get_data(as_text=True)


def test_unrecognised_zip_is_rejected_naming_the_file(app_factory, fixtures_dir: Path) -> None:
    client = app_factory().test_client()
    corrupt = (fixtures_dir / "corrupt_export.zip").read_bytes()
    r = _post_files(client, "/upload", [("mystery.zip", corrupt)])
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "mystery.zip" in body
    assert "recognised export file" in body


def test_failed_set_upload_leaves_previous_set_in_place(app_factory, current_files) -> None:
    """Parse-before-replace, generalised to sets: a bad second upload leaves
    the first set untouched on disk and in memory."""
    flask_app = app_factory()
    client = flask_app.test_client()
    supplied = [(p.name, p.read_bytes()) for p in current_files.values()]
    assert _post_files(client, "/upload", supplied).status_code == 302
    before_export = flask_app.config["EXPORT"]
    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    before_files = {p.name: p.read_bytes() for p in active_set.iterdir()}

    r = _post_files(client, "/upload", [("broken.zip", b"not a zip")])
    assert r.status_code == 400
    assert flask_app.config["EXPORT"] is before_export
    assert {p.name: p.read_bytes() for p in active_set.iterdir()} == before_files


# ---------------------------------------------------------------------------
# Cap knob (record §4)
# ---------------------------------------------------------------------------


def test_cap_knob_reads_env(monkeypatch) -> None:
    monkeypatch.delenv("VIEWER_MAX_UPLOAD_MB", raising=False)
    assert _read_upload_cap_mb() == 200
    monkeypatch.setenv("VIEWER_MAX_UPLOAD_MB", "500")
    assert _read_upload_cap_mb() == 500
    monkeypatch.setenv("VIEWER_MAX_UPLOAD_MB", "not-a-number")
    assert _read_upload_cap_mb() == 200
    monkeypatch.setenv("VIEWER_MAX_UPLOAD_MB", "-3")
    assert _read_upload_cap_mb() == 200


def test_request_backstop_covers_a_full_set(app) -> None:
    """Flask's request-level limit must admit one full set at the per-file
    cap — the server-side per-file rule is enforced while staging."""
    assert app.config["MAX_CONTENT_LENGTH"] == routes_module.UPLOAD_MAX_BYTES * 5


def test_per_file_cap_enforced_on_upload(app_factory, current_files, monkeypatch) -> None:
    client = app_factory().test_client()
    monkeypatch.setattr(routes_module, "UPLOAD_MAX_BYTES", 1024)
    p = current_files["conversations"]
    r = _post_files(client, "/upload", [(p.name, p.read_bytes())])
    assert r.status_code == 400
    assert "per-file cap" in r.get_data(as_text=True)


def test_per_file_cap_enforced_on_inspect(app_factory, current_files, monkeypatch) -> None:
    client = app_factory().test_client()
    monkeypatch.setattr(routes_module, "UPLOAD_MAX_BYTES", 1024)
    p = current_files["conversations"]
    r = _post_files(client, "/inspect", [(p.name, p.read_bytes())])
    assert r.status_code == 400
    assert "per-file cap" in r.get_data(as_text=True)


def test_cap_hint_renders_configured_value(client) -> None:
    body = client.get("/upload").get_data(as_text=True)
    cap_mb = routes_module.UPLOAD_MAX_BYTES // (1024 * 1024)
    assert f"{cap_mb}&nbsp;MB per file" in body


# ---------------------------------------------------------------------------
# Inspect sets (record §4)
# ---------------------------------------------------------------------------


def test_inspect_subset_shows_set_banner_and_degraded_state(app_factory, current_files) -> None:
    client = app_factory().test_client()
    supplied = [
        (current_files["conversations"].name, current_files["conversations"].read_bytes()),
        (current_files["projects"].name, current_files["projects"].read_bytes()),
    ]
    assert _post_files(client, "/inspect", supplied).status_code == 302

    home = client.get("/").get_data(as_text=True)
    assert "Inspecting 2 files: conversations, projects — read-only." in home
    # The degraded banner follows the inspected view.
    assert "Loaded 2 of 4 export categories." in home
    assert "No memories file — the Memory page is unavailable." in home

    # Exit restores the primary (complete) view.
    r = client.post("/inspect/exit", data={"csrf_token": scrape_csrf(client, "/inspect")})
    assert r.status_code == 302
    home = client.get("/").get_data(as_text=True)
    assert "export categories" not in home


def test_inspect_manifest_mismatch_line(app_factory, current_files) -> None:
    client = app_factory().test_client()
    supplied = [
        (current_files["manifest"].name, current_files["manifest"].read_bytes()),
        (current_files["memories"].name, current_files["memories"].read_bytes()),
    ]
    assert _post_files(client, "/inspect", supplied).status_code == 302
    home = client.get("/").get_data(as_text=True)
    assert "The manifest lists 7 files; 1 were supplied." in home


# ---------------------------------------------------------------------------
# Startup pickup, dedup, and continuity (record §5)
# ---------------------------------------------------------------------------


def _relaunch(tmp_path: Path, monkeypatch):
    """create_app(None) against the app_factory's monkeypatched cache tree."""
    return create_app(None, db_path=tmp_path / "cache" / "viewer.sqlite")


def test_startup_picks_up_stored_set_and_dedups_on_fingerprint(
    app_factory, current_files, tmp_path: Path, monkeypatch
) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    supplied = [(p.name, p.read_bytes()) for p in current_files.values()]
    assert _post_files(client, "/upload", supplied).status_code == 302
    assert len(_history_rows(tmp_path / "cache" / "viewer.sqlite")) == 1

    # Relaunch: the set loads from cache/active_export/, and the identical
    # fingerprint records no duplicate startup row.
    relaunched = _relaunch(tmp_path, monkeypatch)
    export = relaunched.config["EXPORT"]
    assert len(export.conversations) == 4
    assert export.user is not None
    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    assert len(rows) == 1

    # The stored manifest keeps feeding the expected-vs-supplied status.
    assert relaunched.config["MANIFEST_STATUS"] == (7, 7)
    assert set(relaunched.config["MANIFEST_CATEGORIES"]) == set(KNOWN_CATEGORIES)


def test_startup_records_set_summary_after_external_change(
    app_factory, current_files, tmp_path: Path, monkeypatch
) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    supplied = [(p.name, p.read_bytes()) for p in current_files.values()]
    assert _post_files(client, "/upload", supplied).status_code == 302

    # Simulate an out-of-band change to the stored set: drop a category.
    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    (active_set / current_files["projects"].name).unlink()

    relaunched = _relaunch(tmp_path, monkeypatch)
    rows = _history_rows(tmp_path / "cache" / "viewer.sqlite")
    assert len(rows) == 2
    source, filename, fingerprint = rows[1]
    assert source == "startup"
    assert filename == (
        "7 files: conversations, memories, light_metadata, frames, artifact_data, "
        "claude_code_cloud, manifest"
    )
    assert fingerprint == set_fingerprint(sorted(active_set.iterdir()))
    assert "projects" not in relaunched.config["EXPORT"].categories


def test_set_of_one_startup_dedups_against_pre_existing_single_zip_row(
    app_factory, fixtures_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    """Continuity (record §5): a pre-set history row recorded the plain zip
    sha; the same archive stored as a set of one fingerprints identically,
    so startup dedup still recognises it."""
    flask_app = app_factory()
    db = tmp_path / "cache" / "viewer.sqlite"
    zip_path = fixtures_dir / SYNTHETIC_EXPORT
    sha = zip_hash(zip_path)

    # A pre-existing row, as an earlier single-zip version recorded it.
    from viewer.history import record_load
    from viewer.parser import load_export

    with sqlite3.connect(db) as conn:
        record_load(
            conn,
            source="startup",
            filename="active_export.zip",
            zip_sha256=sha,
            export=load_export(zip_path),
        )
    assert len(_history_rows(db)) == 1

    # The same archive, now stored as a set of one.
    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    active_set.mkdir(parents=True, exist_ok=True)
    (active_set / "active_export.zip").write_bytes(zip_path.read_bytes())

    _relaunch(tmp_path, monkeypatch)
    assert len(_history_rows(db)) == 1  # fingerprint matched the old digest


def test_corrupt_stored_set_falls_through_with_banner(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    flask_app = app_factory()
    active_set: Path = flask_app.config["ACTIVE_SET_PATH"]
    active_set.mkdir(parents=True, exist_ok=True)
    (active_set / "broken.zip").write_bytes(b"deliberately not a zip")

    relaunched = _relaunch(tmp_path, monkeypatch)
    assert relaunched.config["STORED_EXPORT_FAILED"] is True
    # The broken set was left in place.
    assert (active_set / "broken.zip").is_file()
    body = relaunched.test_client().get("/").get_data(as_text=True)
    assert "load-error-banner" in body


# ---------------------------------------------------------------------------
# Set-summary unit behaviour
# ---------------------------------------------------------------------------


def test_summarise_set_shapes() -> None:
    assert summarise_set({"legacy": ["my-export.zip"]}) == "my-export.zip"
    assert summarise_set({"conversations": ["a.zip"]}) == "1 file: conversations"
    assert (
        summarise_set({"manifest": ["m.json"], "memories": ["b.zip"], "conversations": ["a.zip"]})
        == "3 files: conversations, memories, manifest"
    )


def test_summarise_set_part_multiplicity() -> None:
    """Copy C3 (record §4): a category supplied as N parts appears once with
    a multiplier, counted per file in the total."""
    assert (
        summarise_set(
            {
                "conversations": ["conversations-000.zip", "conversations-001.zip"],
                "projects": ["projects-000.zip"],
                "memories": ["memories-000.zip"],
                "light_metadata": ["light_metadata-000.zip"],
            }
        )
        == "5 files: conversations ×2, projects, memories, light_metadata"
    )
