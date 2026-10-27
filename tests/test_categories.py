"""v1.4.0 model, loader, byte access, cross-references, and search (export-categories
record §6.1, §6.5, §6.6, §10).
"""

from __future__ import annotations

import io
import json
import logging
import pickle
import sqlite3
import zipfile
from pathlib import Path

import pytest

from tests.fixtures import build_fixtures as bf
from tests.real_exports import later_category_problems
from viewer.cache import parser_module_hash, write_cache
from viewer.categories import (
    LAYOUT_MULTI_FILE,
    LAYOUT_NONE,
    LAYOUT_SINGLE_HTML,
    artifact_ids_in,
    html_has_external_resource,
    html_has_script,
    orphan_runs,
    read_member,
    version_member,
    visible_text,
)
from viewer.parser import Export, load_export, load_export_from_bytes_set
from viewer.search import _expected_row_count, index_export, search


@pytest.fixture
def current(fixtures_dir: Path) -> Export:
    return load_export(fixtures_dir / "current")


@pytest.fixture
def edge(fixtures_dir: Path) -> Export:
    return load_export(fixtures_dir / "current_frames_edge")


def _artifact(export: Export, artifact_id: str):
    return next(a for a in export.artifacts if a.id == artifact_id)


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# §6.1 — the model
# ---------------------------------------------------------------------------


def test_three_artifacts_load_with_their_layouts(current: Export) -> None:
    single = _artifact(current, bf.ARTIFACT_SINGLE_UUID)
    multi = _artifact(current, bf.ARTIFACT_MULTI_UUID)
    page = _artifact(current, bf.ARTIFACT_PAGE_UUID)
    assert [v.layout for v in single.versions] == [LAYOUT_SINGLE_HTML]
    assert {v.layout for v in multi.versions} == {LAYOUT_MULTI_FILE}
    assert [v.layout for v in page.versions] == [LAYOUT_NONE]
    assert (single.kind, multi.kind, page.kind) == ("artifact", "artifact", "page")
    assert all(a.visibility == "private" and not a.flags for a in (single, multi, page))
    assert single.part == multi.part == page.part == "frames-000.zip"


def test_versions_display_newest_first_and_active_is_not_assumed_newest(current) -> None:
    multi = _artifact(current, bf.ARTIFACT_MULTI_UUID)
    # artifact.json lists them out of order (V7); display is by created_at.
    assert [v.id for v in multi.versions] == [
        "1790000000-e5f6",
        "1789500000-c3d4",
        "1789000000-a7b8",
    ]
    assert multi.active_version == bf.MULTI_ACTIVE == "1789500000-c3d4"
    assert multi.active.id == multi.active_version
    assert multi.title == "Synthetic reading log"
    assert multi.created_at == multi.versions[-1].created_at


def test_multi_file_version_records_integrity_and_entry(current: Export) -> None:
    version = _artifact(current, bf.ARTIFACT_MULTI_UUID).active
    assert version.entry_path == "index.html"
    assert [f.path for f in version.files] == [
        "index.html",
        "assets/style.css",
        "assets/font.woff2",
        "content/entries.md",
        "content/about.md",
        "capability.json",
    ]
    assert all(f.ok for f in version.files)
    assert version.unlisted_files == []
    assert version.file("assets/style.css").mime_type == "text/css"
    assert version.file("../style.css") is None


def test_page_kind_content_and_comments(current: Export) -> None:
    page = _artifact(current, bf.ARTIFACT_PAGE_UUID)
    assert page.content_member == f"artifacts/{bf.ARTIFACT_PAGE_UUID}/shared-notes.md"
    assert [t.resolved for t in page.comments] == [True, False]
    assert page.comments[0].quoted_text == "First point"
    assert [len(t.comments) for t in page.comments] == [2, 1]


def test_artifact_data_and_orphans(current: Export) -> None:
    by_artifact = {(d.artifact_id, d.name): d for d in current.artifact_data}
    entries = by_artifact[(bf.ARTIFACT_MULTI_UUID, "entries")]
    assert [d.id for d in entries.documents] == ["entry-0001", "entry-0002"]
    assert entries.documents[1].data == {
        "book": "Synthetic Title Two",
        "page_count": 45,
        "tags": ["a", "b"],
    }
    known = {a.id for a in current.artifacts}
    orphans = [d for d in current.artifact_data if d.artifact_id not in known]
    assert [d.artifact_id for d in orphans] == [bf.ARTIFACT_ORPHAN_DATA_UUID]


def test_scheduled_tasks_and_orphan_runs(current: Export) -> None:
    tasks = {t.routine_id: t for t in current.scheduled_tasks}
    cron, once = tasks[bf.ROUTINE_CRON_ID], tasks[bf.ROUTINE_ONCE_ID]
    assert (cron.cron, cron.run_once_at, cron.enabled) == ("0 9 * * 1", None, True)
    assert once.cron is None and once.run_once_at is not None and once.prompt_withheld
    assert len(current.task_runs) == 3
    assert [r.routine_id for r in orphan_runs(current.scheduled_tasks, current.task_runs)] == [
        bf.ROUTINE_ABSENT_ID
    ]
    # Nine fractional digits parse (kept to microseconds).
    assert current.task_runs[0].started_at.microsecond == 123456


def test_artifact_data_without_frames_is_all_orphans(fixtures_dir: Path) -> None:
    blob = (fixtures_dir / "current_new_only" / "artifact_data-000.zip").read_bytes()
    export = load_export_from_bytes_set([("artifact_data-000.zip", blob)])
    assert export.artifacts == [] and len(export.artifact_data) == 3


# ---------------------------------------------------------------------------
# §6.1 / §10 — preserve and flag, one test per case
# ---------------------------------------------------------------------------


def test_size_and_hash_mismatch_missing_and_unlisted_files(edge: Export, caplog) -> None:
    version = _artifact(edge, bf.ARTIFACT_INTEGRITY_UUID).active
    results = {f.path: (f.present, f.size_match, f.hash_match) for f in version.files}
    assert results["assets/style.css"] == (True, True, False)
    assert results["assets/font.woff2"] == (True, False, True)
    assert results["content/about.md"] == (False, False, False)
    assert version.unlisted_files == ["extra/unlisted.txt"]
    assert "unlisted_files" in version.flags


def test_zero_versions_and_missing_active_version(edge: Export) -> None:
    empty = _artifact(edge, bf.ARTIFACT_NO_VERSIONS_UUID)
    assert empty.versions == [] and empty.active is None and "no_versions" in empty.flags
    dangling = _artifact(edge, bf.ARTIFACT_ACTIVE_MISSING_UUID)
    assert "active_version_missing" in dangling.flags
    assert dangling.active is dangling.versions[0]  # falls back to the newest
    assert dangling.versions[0].layout == LAYOUT_NONE
    assert "no_files" in dangling.versions[0].flags


def test_unknown_kind_and_visibility_are_preserved_raw(edge: Export) -> None:
    odd = _artifact(edge, bf.ARTIFACT_UNKNOWN_KIND_UUID)
    assert (odd.kind, odd.visibility) == ("widget", "link_shared")
    assert {"unknown_kind", "unknown_visibility"} <= set(odd.flags)


def test_unreadable_comments_and_missing_content(edge: Export) -> None:
    page = _artifact(edge, bf.ARTIFACT_BAD_COMMENTS_UUID)
    assert page.comments is None and "unreadable_comments" in page.flags
    assert page.content_member is None


def test_duplicate_artifact_id_across_parts_kept_once(fixtures_dir: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "current_frames_two_part")
    ids = [a.id for a in export.artifacts]
    assert ids.count(bf.ARTIFACT_SINGLE_UUID) == 1
    assert _artifact(export, bf.ARTIFACT_SINGLE_UUID).title == "Synthetic colour card"
    assert any("duplicate artifact id" in r.getMessage() for r in caplog.records)
    assert len(ids) == 3


def test_artifact_directory_without_descriptor_is_preserved() -> None:
    aid = "00000000-0000-0000-0000-0000000a0d00"
    blob = _zip(
        {
            f"artifacts/{bf.ARTIFACT_SINGLE_UUID}/artifact.json": json.dumps(
                {
                    "id": bf.ARTIFACT_SINGLE_UUID,
                    "kind": "artifact",
                    "visibility": "private",
                    "versions": [],
                    "active_version": None,
                }
            ).encode(),
            f"artifacts/{aid}/versions/1789000000-abcd.html": b"<!doctype html><p>x</p>",
        }
    )
    export = load_export_from_bytes_set([("frames-000.zip", blob)])
    orphan = _artifact(export, aid)
    assert "no_descriptor" in orphan.flags
    assert "unattributed_version_files" in orphan.flags


def test_owner_account_mismatch_is_logged_never_fatal(fixtures_dir: Path, caplog) -> None:
    frames = _zip(
        {
            f"artifacts/{bf.ARTIFACT_SINGLE_UUID}/artifact.json": json.dumps(
                {
                    "id": bf.ARTIFACT_SINGLE_UUID,
                    "kind": "artifact",
                    "visibility": "private",
                    "owner_account": "00000000-0000-0000-0000-00000000bbbb",
                    "versions": [],
                    "active_version": None,
                }
            ).encode()
        }
    )
    light = (fixtures_dir / "current" / "light_metadata-000.zip").read_bytes()
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export_from_bytes_set(
            [("light_metadata-000.zip", light), ("frames-000.zip", frames)]
        )
    assert len(export.artifacts) == 1
    assert any("artifact owner mismatch" in r.getMessage() for r in caplog.records)


def test_real_data_invariant_helper_counts_violations(edge: Export, current: Export) -> None:
    assert later_category_problems(current) == {
        "unresolved active versions": 0,
        "multi-file versions failing integrity": 0,
        "runs neither resolved nor preserved": 0,
    }
    problems = later_category_problems(edge)
    assert problems["unresolved active versions"] == 1
    assert problems["multi-file versions failing integrity"] == 1


# ---------------------------------------------------------------------------
# §6.1 — bytes stay out of the parse cache (the signature-drop pattern)
# ---------------------------------------------------------------------------

PAYLOAD_SENTINEL = b"U1lOVEhFVElDLVBBWUxPQUQtU0VOVElORUw"


def _frames_with_payload(size: int) -> bytes:
    """A multi-file artifact whose script, stylesheet, and font carry the sentinel."""
    js = b"/*" + PAYLOAD_SENTINEL + b"*/" + b"x" * size
    css = b"/*" + PAYLOAD_SENTINEL + b"*/ body{}"
    font = PAYLOAD_SENTINEL + b"\x00" * 64
    html = (
        b"<!doctype html><title>t</title><script>/*" + PAYLOAD_SENTINEL + b"*/</script><p>Shown</p>"
    )
    files = [
        ("index.html", "text/html", html),
        ("app.js", "text/javascript", js),
        ("style.css", "text/css", css),
        ("font.woff2", "font/woff2", font),
    ]
    vid = "1789000000-abcd"
    base = f"artifacts/{bf.ARTIFACT_MULTI_UUID}"
    members = {
        f"{base}/artifact.json": json.dumps(
            {
                "id": bf.ARTIFACT_MULTI_UUID,
                "kind": "artifact",
                "visibility": "private",
                "versions": [
                    {
                        "id": vid,
                        "title": "t",
                        "description": "d",
                        "created_at": "2026-09-09T00:00:00+00:00",
                    }
                ],
                "active_version": vid,
            }
        ).encode(),
        f"{base}/versions/{vid}.files.json": bf._files_json(files),
    }
    for path, _mime, data in files:
        members[f"{base}/versions/{vid}/{path}"] = data
    return _zip(members)


def test_artifact_payload_never_reaches_the_model_or_the_pickle(tmp_path: Path) -> None:
    small = load_export_from_bytes_set([("frames-000.zip", _frames_with_payload(10))])
    large = load_export_from_bytes_set([("frames-000.zip", _frames_with_payload(500_000))])
    assert "Shown" in small.artifacts[0].search_text
    pkl = tmp_path / "parsed.pkl"
    write_cache(pkl, ("fingerprint", parser_module_hash()), large)
    assert PAYLOAD_SENTINEL not in pkl.read_bytes()
    assert PAYLOAD_SENTINEL.decode() not in large.artifacts[0].search_text
    # Growing the bundle by ~500 KB doesn't grow the pickle.
    assert abs(len(pickle.dumps(large)) - len(pickle.dumps(small))) < 64


# ---------------------------------------------------------------------------
# §6.1 — on-demand byte access
# ---------------------------------------------------------------------------


def test_read_member_from_disk_and_from_memory(fixtures_dir: Path, current: Export) -> None:
    multi = _artifact(current, bf.ARTIFACT_MULTI_UUID)
    member = version_member(multi.id, multi.active, "assets/style.css")
    path = fixtures_dir / "current" / multi.part
    from_disk = read_member(lambda part: path if part == multi.part else None, multi.part, member)
    blob = path.read_bytes()
    from_memory = read_member(lambda part: io.BytesIO(blob), multi.part, member)
    assert from_disk == from_memory == bf.MULTI_CSS.encode()
    assert read_member(lambda part: None, multi.part, member) is None
    assert read_member(lambda part: path, multi.part, "artifacts/none") is None


def test_entry_page_probes(current: Export, fixtures_dir: Path) -> None:
    path = fixtures_dir / "current" / "frames-000.zip"
    single = _artifact(current, bf.ARTIFACT_SINGLE_UUID)
    multi = _artifact(current, bf.ARTIFACT_MULTI_UUID)
    s_html = read_member(lambda p: path, single.part, version_member(single.id, single.active))
    m_html = read_member(
        lambda p: path, multi.part, version_member(multi.id, multi.active, "index.html")
    )
    assert (html_has_script(s_html), html_has_external_resource(s_html)) == (False, True)
    assert (html_has_script(m_html), html_has_external_resource(m_html)) == (True, False)
    assert "scriptRan" not in visible_text(m_html)


# ---------------------------------------------------------------------------
# §6.5 — cross-references
# ---------------------------------------------------------------------------


def test_mentions_come_from_input_and_structured_content(current: Export) -> None:
    conv = "00000000-0000-0000-0000-0000000000e1"
    assert current.artifact_mentions == {
        bf.ARTIFACT_MULTI_UUID: [conv],
        bf.ARTIFACT_PAGE_UUID: [conv],
    }


def test_prose_mentions_are_not_links() -> None:
    known = {bf.ARTIFACT_SINGLE_UUID}
    assert artifact_ids_in({"text": f"see {bf.ARTIFACT_SINGLE_UUID}"}, known) == [
        bf.ARTIFACT_SINGLE_UUID
    ]
    # The fixture's publish result names the multi-file artifact in its prose too, but
    # mentions are built only from input and structured_content (asserted above); a
    # prose-only id never links.
    assert artifact_ids_in(None, known) == []
    assert artifact_ids_in({"x": 1}, set()) == []


# ---------------------------------------------------------------------------
# §6.6 — search kinds
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(current: Export):
    c = sqlite3.connect(":memory:")
    index_export(c, current)
    yield c
    c.close()


def test_invariant_counts_the_new_kinds(current: Export, conn) -> None:
    kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM search GROUP BY kind").fetchall())
    assert (kinds["artifact"], kinds["scheduled_task"]) == (3, 2)
    assert sum(kinds.values()) == _expected_row_count(current)


def test_artifact_hits_carry_their_id(conn) -> None:
    hits = [h for h in search(conn, "colour card").hits if h.kind == "artifact"]
    assert [h.entity_id for h in hits] == [bf.ARTIFACT_SINGLE_UUID]
    page = [h for h in search(conn, "Second point").hits if h.kind == "artifact"]
    assert [h.entity_id for h in page] == [bf.ARTIFACT_PAGE_UUID]
    markdown = [h for h in search(conn, "First synthetic entry").hits if h.kind == "artifact"]
    assert [h.entity_id for h in markdown] == [bf.ARTIFACT_MULTI_UUID]


def test_scripts_and_styles_are_not_indexed(conn) -> None:
    assert not [h for h in search(conn, "scriptRan").hits if h.kind == "artifact"]
    assert not [h for h in search(conn, "prefers").hits if h.kind == "artifact"]


def test_scheduled_task_hits(conn) -> None:
    hits = [h for h in search(conn, "repository").hits if h.kind == "scheduled_task"]
    assert [(h.title, h.entity_id) for h in hits] == [
        ("Synthetic weekly tidy-up", bf.ROUTINE_CRON_ID)
    ]


def test_upload_points_sources_at_the_stored_set(app_factory, fixtures_dir: Path) -> None:
    from tests.test_upload_sets import _post_files
    from viewer.routes import artifact_part_opener

    flask_app = app_factory()
    client = flask_app.test_client()
    files = [(p.name, p.read_bytes()) for p in sorted((fixtures_dir / "current").iterdir())]
    assert _post_files(client, "/upload", files).status_code == 302
    active_set = flask_app.config["ACTIVE_SET_PATH"]
    export = flask_app.config["EXPORT"]
    assert all(Path(p).parent == active_set for p in export.source_paths)
    assert "active_export.stage" not in client.get("/").get_data(as_text=True)
    with flask_app.test_request_context():
        opener = artifact_part_opener()
        assert opener("frames-000.zip") == active_set / "frames-000.zip"
        assert opener("missing.zip") is None


def test_inspect_keeps_frames_bytes_in_memory(app_factory, fixtures_dir: Path) -> None:
    from tests.test_upload_sets import _post_files
    from viewer.inspect import get_inspection
    from viewer.routes import artifact_part_opener

    flask_app = app_factory()
    client = flask_app.test_client()
    files = [(p.name, p.read_bytes()) for p in sorted((fixtures_dir / "current").glob("*.zip"))]
    assert _post_files(client, "/inspect", files).status_code == 302
    state = get_inspection(flask_app)
    assert set(state.part_blobs) == {"frames-000.zip"}
    with flask_app.test_request_context():
        source = artifact_part_opener()("frames-000.zip")
        assert isinstance(source, io.BytesIO)
