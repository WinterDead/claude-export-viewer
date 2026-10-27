"""v1.3.1 behaviours from ``docs/export-categories-design.md`` §3–§5, §7, §8.

Every UI-bearing behaviour here is exercised through the real rendered markup: uploads
and inspections are submitted through the page's own multipart form, the hide-legacy-memory
control through its own rendered form, and copy is matched against the record's appendix.
"""

from __future__ import annotations

import io
import re
import shutil
import sqlite3
import zipfile
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.test_upload_sets import _post_files
from viewer.export import ExportOptions, serialise_conversation
from viewer.parser import (
    KNOWN_CATEGORIES,
    DocumentBlock,
    load_export,
    sniff_container,
)

D1 = (
    "{filename} isn't a file this version of the viewer recognises, so it was skipped. "
    "The rest of your export loaded."
)
# D2 as amended by E4 (v1.4.0 review): the category's display label.
D2 = (
    "{filename} looks like your export's {label} file but couldn't be read, so it was "
    "skipped. The rest of your export loaded."
)
D4 = (
    "Current exports can also include files for published artifacts, artifact data, and "
    "Claude Code scheduled tasks. This version accepts them with the rest of your export "
    "and keeps them, but doesn't display them yet."
)
D5_HEADING = "Legacy memory (no longer updated)"
D5_NOTE = (
    "This is the summary from Claude's previous memory system. It stopped updating when "
    "your account moved to memory files, so it may not match them."
)
D7_WARNING = (
    "These links are personal and expire about 24 hours after the export was created. "
    "Don't share them. The viewer doesn't download anything itself and doesn't keep your "
    "manifest."
)
CONV_ARTIFACT = "00000000-0000-0000-0000-0000000000e1"


def _text(html: str) -> str:
    """Whitespace-normalised visible text, tags removed."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def current_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "current"


@pytest.fixture
def full_set(current_dir: Path) -> list[tuple[str, bytes]]:
    """The eight-file delivery: manifest plus seven category ZIPs."""
    return [(p.name, p.read_bytes()) for p in sorted(current_dir.iterdir())]


UNKNOWN_ZIP = (
    "future_category-000.zip",
    _zip_bytes({"future_category/records.json": b'{"records": []}'}),
)
BROKEN_FRAMES = (
    "frames-000.zip",
    _zip_bytes({"artifacts/00000000-0000-0000-0000-0000000a0bad/artifact.json": b"{not json"}),
)


# ---------------------------------------------------------------------------
# §3 — signatures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("frames-000.zip", "frames"),
        ("artifact_data-000.zip", "artifact_data"),
        ("claude_code_cloud-000.zip", "claude_code_cloud"),
    ],
)
def test_later_category_signatures(current_dir: Path, name: str, expected: str) -> None:
    with zipfile.ZipFile(current_dir / name) as zf:
        assert sniff_container(set(zf.namelist())) == expected


def test_empty_frames_zip_matches_no_signature(fixtures_dir: Path) -> None:
    with zipfile.ZipFile(fixtures_dir / "current_empty_frames" / "frames-000.zip") as zf:
        assert sniff_container(set(zf.namelist())) is None


def test_new_categories_alone_load(fixtures_dir: Path) -> None:
    export = load_export(fixtures_dir / "current_new_only")
    assert export.categories == ("frames", "artifact_data", "claude_code_cloud")
    assert export.conversations == [] and export.user is None


# ---------------------------------------------------------------------------
# §3 A6 — unrecognised files are skipped, not fatal (D1); §5.1 D2
# ---------------------------------------------------------------------------


def test_full_eight_file_set_through_the_real_upload_form(app_factory, full_set) -> None:
    """The regression this version exists to fix: v1.3.0 refused this upload."""
    assert len(full_set) == 8
    flask_app = app_factory()
    client = flask_app.test_client()
    assert _post_files(client, "/upload", full_set).status_code == 302
    assert flask_app.config["EXPORT"].categories == KNOWN_CATEGORIES
    home = client.get("/").get_data(as_text=True)
    assert "export categories" not in home
    assert "load-notice-banner" not in home


def test_unknown_ninth_zip_upload_loads_with_d1(app_factory, full_set) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    r = _post_files(client, "/upload", [*full_set, UNKNOWN_ZIP])
    assert r.status_code == 302
    assert flask_app.config["EXPORT"].categories == KNOWN_CATEGORIES
    stored = {p.name for p in flask_app.config["ACTIVE_SET_PATH"].iterdir()}
    assert UNKNOWN_ZIP[0] not in stored  # skipped files are never stored
    home = client.get("/").get_data(as_text=True)
    assert D1.format(filename=UNKNOWN_ZIP[0]) in _text(home)
    # Shown once: the next page no longer carries it.
    assert D1.format(filename=UNKNOWN_ZIP[0]) not in _text(client.get("/").get_data(as_text=True))


def test_unknown_zip_inspect_loads_with_d1(app_factory, full_set) -> None:
    client = app_factory().test_client()
    assert _post_files(client, "/inspect", [*full_set, UNKNOWN_ZIP]).status_code == 302
    home = client.get("/").get_data(as_text=True)
    assert "inspect-banner" in home
    assert D1.format(filename=UNKNOWN_ZIP[0]) in _text(home)


def test_empty_frames_zip_is_skipped_with_d1(app_factory, current_dir, fixtures_dir) -> None:
    client = app_factory().test_client()
    empty = fixtures_dir / "current_empty_frames" / "frames-000.zip"
    files = [
        (p.name, p.read_bytes())
        for p in sorted(current_dir.glob("*.zip"))
        if not p.name.startswith("frames")
    ]
    assert (
        _post_files(client, "/upload", [*files, ("frames-000.zip", empty.read_bytes())]).status_code
        == 302
    )
    assert D1.format(filename="frames-000.zip") in _text(client.get("/").get_data(as_text=True))


@pytest.mark.parametrize("page", ["/upload", "/inspect"])
def test_all_unrecognised_submission_still_errors(app_factory, page) -> None:
    client = app_factory().test_client()
    r = _post_files(client, page, [UNKNOWN_ZIP, ("other.zip", _zip_bytes({"x.txt": b"x"}))])
    assert r.status_code == 400
    assert f"{UNKNOWN_ZIP[0]} isn't a recognised export file." in _text(r.get_data(as_text=True))


@pytest.mark.parametrize("page", ["/upload", "/inspect"])
def test_unreadable_later_category_is_skipped_with_d2(app_factory, current_dir, page) -> None:
    flask_app = app_factory()
    client = flask_app.test_client()
    files = [
        (p.name, p.read_bytes())
        for p in sorted(current_dir.glob("*.zip"))
        if not p.name.startswith("frames")
    ]
    assert _post_files(client, page, [*files, BROKEN_FRAMES]).status_code == 302
    home = client.get("/").get_data(as_text=True)
    assert D2.format(filename="frames-000.zip", label="published artifacts") in _text(home)
    if page == "/upload":
        stored = {p.name for p in flask_app.config["ACTIVE_SET_PATH"].iterdir()}
        assert "frames-000.zip" not in stored


def test_unreadable_category_alone_is_refused(app_factory) -> None:
    client = app_factory().test_client()
    r = _post_files(client, "/upload", [BROKEN_FRAMES])
    assert r.status_code == 400
    assert "Couldn't parse archive: frames-000.zip couldn't be read." in _text(
        r.get_data(as_text=True)
    )


def test_path_loader_skips_unrecognised_zip_beside_categories(tmp_path, current_dir) -> None:
    shutil.copytree(current_dir, tmp_path / "set")
    (tmp_path / "set" / UNKNOWN_ZIP[0]).write_bytes(UNKNOWN_ZIP[1])
    assert load_export(tmp_path / "set").categories == KNOWN_CATEGORIES


# ---------------------------------------------------------------------------
# §4 A7 — the banner generalises (D3)
# ---------------------------------------------------------------------------


def test_manifest_banner_names_missing_later_categories(app_factory, current_dir) -> None:
    client = app_factory().test_client()
    manifest = next(current_dir.glob("*.json"))
    core = [
        (p.name, p.read_bytes())
        for p in sorted(current_dir.glob("*.zip"))
        if p.name.split("-")[0] in ("conversations", "projects", "memories", "light_metadata")
    ]
    assert (
        _post_files(client, "/upload", [(manifest.name, manifest.read_bytes()), *core]).status_code
        == 302
    )
    text = _text(client.get("/").get_data(as_text=True))
    assert "Loaded 4 of 7 export categories." in text
    assert "No published artifacts file — published artifacts are unavailable." in text
    assert "No artifact data file — data saved by published artifacts is unavailable." in text
    assert "No Claude Code file — scheduled tasks and their run history are unavailable." in text
    assert "The manifest lists 7 files; 4 were supplied." in text


def test_manifest_banner_reports_unknown_category_by_raw_name(app_factory, current_dir) -> None:
    import json

    manifest = json.loads(next(current_dir.glob("*.json")).read_text(encoding="utf-8"))
    manifest["data_files"].append(
        {
            "batch_index": 7,
            "category": "future_category",
            "part": 0,
            "filename": "future_category-000.zip",
            "export_url": "https://example.invalid/download/future_category-000.zip",
        }
    )
    manifest["total_files"] = len(manifest["data_files"])
    files = [(p.name, p.read_bytes()) for p in sorted(current_dir.glob("*.zip"))]
    client = app_factory().test_client()
    r = _post_files(
        client, "/upload", [("manifest.json", json.dumps(manifest).encode()), *files, UNKNOWN_ZIP]
    )
    assert r.status_code == 302
    text = _text(client.get("/").get_data(as_text=True))
    assert "Loaded 7 of 8 export categories." in text
    assert "No future_category file — this version of the viewer doesn't use it yet." in text


def test_without_manifest_later_categories_never_raise_the_banner(app_factory, current_dir) -> None:
    client = app_factory().test_client()
    core = [
        (p.name, p.read_bytes())
        for p in sorted(current_dir.glob("*.zip"))
        if p.name.split("-")[0] in ("conversations", "projects", "memories", "light_metadata")
    ]
    assert _post_files(client, "/upload", core).status_code == 302
    assert "export categories" not in client.get("/").get_data(as_text=True)


def test_new_categories_alone_count_against_the_core_four(app_factory, fixtures_dir) -> None:
    client = app_factory().test_client()
    files = [
        (p.name, p.read_bytes()) for p in sorted((fixtures_dir / "current_new_only").iterdir())
    ]
    assert _post_files(client, "/upload", files).status_code == 302
    text = _text(client.get("/").get_data(as_text=True))
    assert "Loaded 0 of 4 export categories." in text
    assert "No published artifacts file" not in text


# ---------------------------------------------------------------------------
# §5.1 D4 — kept, not displayed
# ---------------------------------------------------------------------------


def test_kept_not_displayed_line_retired_in_v140(app_factory, full_set) -> None:
    """D4 shipped in v1.3.1 and is removed in v1.4.0, which displays all three
    categories (record appendix D4)."""
    client = app_factory().test_client()
    assert _post_files(client, "/upload", full_set).status_code == 302
    assert D4 not in _text(client.get("/").get_data(as_text=True))


# ---------------------------------------------------------------------------
# §5.3 D6 — the document block
# ---------------------------------------------------------------------------


def test_document_blocks_are_typed(current_dir: Path) -> None:
    export = load_export(current_dir)
    conv = next(c for c in export.conversations if c.uuid == CONV_ARTIFACT)
    docs = [b for m in conv.messages for b in m.content if isinstance(b, DocumentBlock)]
    assert [d.title for d in docs] == ["Synthetic brief.pdf", None]
    assert all(d.file_uuid for d in docs)


def test_document_block_renders_as_a_labelled_file_line(app_factory) -> None:
    client = app_factory("current").test_client()
    body = client.get(f"/conversations/{CONV_ARTIFACT}").get_data(as_text=True)
    cards = re.findall(r'<aside class="block-card block-document">(.*?)</aside>', body, re.S)
    assert [_text(c) for c in cards] == [
        "Document Synthetic brief.pdf The file itself isn't included in the export.",
        "Document Untitled document The file itself isn't included in the export.",
    ]
    assert "block-unknown" not in body


def test_document_block_export_line(current_dir: Path) -> None:
    export = load_export(current_dir)
    conv = next(c for c in export.conversations if c.uuid == CONV_ARTIFACT)
    md = serialise_conversation(conv, conv.canonical_path, ExportOptions())
    assert "> **[Document: Synthetic brief.pdf]**" in md
    assert "> **[Document: Untitled document]**" in md
    assert "Unrecognised content block: document" not in md


# ---------------------------------------------------------------------------
# §5.4 — legacy login history
# ---------------------------------------------------------------------------


def test_legacy_archive_loads_root_login_history(fixtures_dir: Path) -> None:
    export = load_export(fixtures_dir / "synthetic_export_login_history.zip")
    assert len(export.login_events) == 3
    assert load_export(fixtures_dir / "synthetic_export.zip").login_events == []


def test_account_page_shows_legacy_login_history(app_factory) -> None:
    client = app_factory("synthetic_export_login_history.zip").test_client()
    body = client.get("/account").get_data(as_text=True)
    for ip in ("203.0.113.10", "198.51.100.7", "192.0.2.55"):
        assert ip in body


# ---------------------------------------------------------------------------
# §7 D5 — legacy memory
# ---------------------------------------------------------------------------


def _memory_page(client) -> str:
    return client.get("/memory").get_data(as_text=True)


def test_legacy_memory_orders_groups_and_collapses(app_factory) -> None:
    body = _memory_page(app_factory("current").test_client())
    files = body.index("Memory files")
    projects = body.index("Project memories")
    legacy = body.index(D5_HEADING)
    assert files < projects < legacy
    tag = re.search(r'<details class="memory-group memory-group-legacy"[^>]*>', body).group(0)
    assert " open" not in tag
    assert D5_NOTE in _text(body)
    assert "<h2>Conversation memory</h2>" not in body


def test_without_memory_files_the_summary_is_the_memory(app_factory) -> None:
    body = _memory_page(app_factory().test_client())  # legacy archive: no memory files
    assert "<h2>Conversation memory</h2>" in body
    assert D5_HEADING not in body
    assert "Hide legacy memory" not in body


def test_search_hit_carries_the_legacy_label(app_factory) -> None:
    client = app_factory("current").test_client()
    body = client.get("/search?q=synthetic+global").get_data(as_text=True)
    group = re.search(r'#global-memory">\s*(.*?)\s*</a>', body, re.S)
    assert group and _text(group.group(1)) == "Legacy memory"


class _ControlForm(HTMLParser):
    """The Memory page's hide-legacy-memory form as rendered."""

    def __init__(self) -> None:
        super().__init__()
        self.action = None
        self.inputs: list[dict] = []
        self._in = False

    def handle_starttag(self, tag, attrs_list):
        attrs = dict(attrs_list)
        if tag == "form" and "legacy-memory-form" in (attrs.get("class") or ""):
            self._in, self.action = True, attrs.get("action")
        elif tag == "input" and self._in:
            self.inputs.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self._in = False


def _submit_control(client, *, checked: bool):
    """Submit the rendered control the way a browser would: the checkbox
    first (only when checked), then the hidden fallback."""
    form = _ControlForm()
    form.feed(_memory_page(client))
    assert form.action == "/memory/legacy"
    data: dict[str, list[str] | str] = {}
    values: list[str] = []
    for attrs in form.inputs:
        if attrs.get("type") == "checkbox" and not checked:
            continue
        if attrs.get("name") == "hide_legacy_memory":
            values.append(attrs["value"])
        elif attrs.get("name"):
            data[attrs["name"]] = attrs["value"]
    data["hide_legacy_memory"] = values
    return client.post(form.action, data=data)


def test_hide_setting_through_the_rendered_control(app_factory) -> None:
    client = app_factory("current").test_client()
    body = _memory_page(client)
    assert re.search(r'<input type="checkbox" name="hide_legacy_memory" value="1"[^>]*>', body)
    assert "Hide legacy memory" in _text(body)

    r = _submit_control(client, checked=True)
    assert r.status_code == 302
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("viewer_legacy_memory=hide")
    assert "HttpOnly" in cookie
    body = _memory_page(client)
    assert D5_HEADING not in body
    assert "checked" in re.search(r'<input type="checkbox"[^>]*>', body).group(0)
    search = client.get("/search?q=synthetic+global").get_data(as_text=True)
    assert "#global-memory" not in search

    _submit_control(client, checked=False)
    assert D5_HEADING in _memory_page(client)


def test_hide_setting_never_hides_a_summary_without_memory_files(app_factory) -> None:
    client = app_factory().test_client()  # legacy archive: no memory files
    client.set_cookie("viewer_legacy_memory", "hide")
    body = _memory_page(client)
    assert "<h2>Conversation memory</h2>" in body
    assert 'id="global-memory"' in body
    search = client.get("/search?q=synthetic+global").get_data(as_text=True)
    assert "#global-memory" in search


def test_inspect_obeys_section_7_regardless_of_the_cookie(
    app_factory, current_dir, fixtures_dir
) -> None:
    client = app_factory("current").test_client()
    client.set_cookie("viewer_legacy_memory", "hide")
    assert D5_HEADING not in _memory_page(client)  # primary: hidden

    # Inspecting an export with memory files: de-emphasised, never hidden, no control.
    files = [(p.name, p.read_bytes()) for p in sorted(current_dir.glob("*.zip"))]
    assert _post_files(client, "/inspect", files).status_code == 302
    body = _memory_page(client)
    assert D5_HEADING in body
    assert "Hide legacy memory" not in body

    # Inspecting an export without memory files: shown as the memory.
    legacy = fixtures_dir / "synthetic_export.zip"
    assert _post_files(client, "/inspect", [(legacy.name, legacy.read_bytes())]).status_code == 302
    body = _memory_page(client)
    assert "<h2>Conversation memory</h2>" in body
    assert "Hide legacy memory" not in body


# ---------------------------------------------------------------------------
# §8 D7 — the manifest links page
# ---------------------------------------------------------------------------


def test_manifest_page_copy(app_factory) -> None:
    client = app_factory().test_client()
    text = _text(client.get("/manifest").get_data(as_text=True))
    assert "Download links" in text
    assert (
        "Open the manifest file from your export email to see its download links. Download "
        "each file, then upload them all together on the Upload page."
    ) in text
    assert "Manifest file" in text and "Show download links" in text
    upload = _text(client.get("/upload").get_data(as_text=True))
    assert "Only have the manifest file? Get your download links." in upload


def test_manifest_page_lists_links_and_writes_nothing(app_factory, current_dir, tmp_path) -> None:
    import json

    manifest = json.loads(next(current_dir.glob("*.json")).read_text(encoding="utf-8"))
    manifest["data_files"][1]["export_url"] = "http://example.invalid/insecure.zip"
    manifest["data_files"][2]["export_url"] = "javascript:alert(1)"
    client = app_factory().test_client()
    client.get("/")
    before = _snapshot(tmp_path / "cache")

    r = _post_files(client, "/manifest", [("manifest.json", json.dumps(manifest).encode())])
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    text = _text(body)
    assert "Export created " in text
    assert D7_WARNING in text
    assert "File Category Download" in text
    anchors = re.findall(r'<a href="([^"]+)" rel="noopener noreferrer">Download</a>', body)
    assert len(anchors) == 5 and all(a.startswith("https://") for a in anchors)
    assert text.count("(link not shown)") == 2
    assert "javascript:" not in body

    assert _snapshot(tmp_path / "cache") == before
    with sqlite3.connect(tmp_path / "cache" / "viewer.sqlite") as c:
        assert c.execute("SELECT COUNT(*) FROM upload_history").fetchone()[0] == 0


def test_manifest_page_rejects_other_files(app_factory) -> None:
    client = app_factory().test_client()
    r = _post_files(client, "/manifest", [("notes.json", b'{"hello": 1}')])
    assert r.status_code == 400
    assert "That file isn't an export manifest." in _text(r.get_data(as_text=True))


def test_hidden_legacy_hit_leaves_the_match_count(app_factory) -> None:
    client = app_factory("current").test_client()

    def count() -> int:
        text = _text(client.get("/search?q=synthetic+global").get_data(as_text=True))
        if "No matches for" in text:
            return 0
        return int(re.search(r"(\d+) match", text).group(1))

    shown = count()
    client.set_cookie("viewer_legacy_memory", "hide")
    assert count() == shown - 1
