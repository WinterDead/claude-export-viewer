"""Artifacts and Scheduled tasks pages and the sandboxed preview (v1.4.0).

``docs/export-categories-design.md`` §6.2–§6.5, copy D8/D9/U4. The record §6.3 security
outcomes are each pinned by their own verify-negative test, against the real responses.
"""

from __future__ import annotations

import io
import re
import zipfile
from html import unescape
from pathlib import Path

import pytest

from tests.fixtures import build_fixtures as bf
from tests.test_upload_sets import _post_files

SINGLE, MULTI, PAGE = bf.ARTIFACT_SINGLE_UUID, bf.ARTIFACT_MULTI_UUID, bf.ARTIFACT_PAGE_UUID
CONV_ARTIFACT = "00000000-0000-0000-0000-0000000000e1"


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def client(app_factory):
    return app_factory("current").test_client()


@pytest.fixture
def edge_client(app_factory):
    return app_factory("current_frames_edge").test_client()


def _file_url(aid: str, vid: str, path: str) -> str:
    return f"/artifacts/{aid}/v/{vid}/f/{path}"


def _all_file_urls(export) -> list[tuple[str, str]]:
    """(url, recorded MIME type) for every servable file of every version."""
    urls = []
    for a in export.artifacts:
        for v in a.versions:
            if v.layout == "single_html":
                urls.append((_file_url(a.id, v.id, f"{v.id}.html"), "text/html"))
            for f in v.files:
                if f.present:
                    urls.append((_file_url(a.id, v.id, f.path), f.mime_type))
    return urls


# ---------------------------------------------------------------------------
# §6.3 — the security boundary, one verify-negative test per outcome
# ---------------------------------------------------------------------------


def test_preview_iframe_sandbox_lacks_allow_scripts_and_allow_same_origin(client) -> None:
    for aid in (SINGLE, MULTI):
        body = client.get(f"/artifacts/{aid}").get_data(as_text=True)
        frames = re.findall(r"<iframe\b[^>]*>", body)
        assert len(frames) == 1
        m = re.search(r'\bsandbox="([^"]*)"', frames[0])
        assert m is not None, "the preview must carry a sandbox attribute"
        tokens = m.group(1).split()
        assert "allow-scripts" not in tokens
        assert "allow-same-origin" not in tokens
        assert tokens == []


def _directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        bits = part.split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


def test_every_artifact_file_response_carries_a_sandboxing_csp(client) -> None:
    export = client.application.config["EXPORT"]
    urls = _all_file_urls(export)
    assert len(urls) == 1 + 3 * 6
    fetch = (
        "default-src",
        "script-src",
        "style-src",
        "img-src",
        "font-src",
        "media-src",
        "connect-src",
        "frame-src",
        "child-src",
        "worker-src",
        "manifest-src",
        "object-src",
    )
    for url, _mime in urls:
        r = client.get(url)
        assert r.status_code == 200, url
        csp = r.headers["Content-Security-Policy"]
        d = _directives(csp)
        assert "sandbox" in d and d["sandbox"] == []  # no tokens: nothing allowed
        assert d["script-src"] == ["'none'"] and d["default-src"] == ["'none'"]
        for name in fetch:
            for source in d.get(name, []):
                assert source in ("'none'", "'self'", "'unsafe-inline'", "data:"), (name, source)
        assert "http" not in csp and "*" not in csp
        assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_traversal_paths_404(client) -> None:
    vid = bf.MULTI_ACTIVE
    for path in ("../artifact.json", "assets/../../artifact.json", "../../../../app.py"):
        assert client.get(_file_url(MULTI, vid, path)).status_code == 404


def test_absolute_paths_404(client) -> None:
    vid = bf.MULTI_ACTIVE
    assert client.get(f"/artifacts/{MULTI}/v/{vid}/f//etc/hosts").status_code == 404
    assert client.get(_file_url(MULTI, vid, "C:/Windows/win.ini")).status_code == 404


def test_percent_encoded_paths_404(client) -> None:
    vid = bf.MULTI_ACTIVE
    for path in ("%2e%2e/artifact.json", "..%2fartifact.json", "assets%2fstyle.css%00"):
        assert client.get(_file_url(MULTI, vid, path)).status_code == 404


def test_backslash_paths_404(client) -> None:
    vid = bf.MULTI_ACTIVE
    for path in ("assets\\style.css", "..\\artifact.json"):
        assert client.get(_file_url(MULTI, vid, path)).status_code == 404


def test_unlisted_and_missing_paths_404(edge_client) -> None:
    vid = "1789200000-0b0b"
    aid = bf.ARTIFACT_INTEGRITY_UUID
    assert edge_client.get(_file_url(aid, vid, "extra/unlisted.txt")).status_code == 404
    assert edge_client.get(_file_url(aid, vid, "content/about.md")).status_code == 404
    # A mismatched file is still served (record §10), carrying the headers.
    r = edge_client.get(_file_url(aid, vid, "assets/style.css"))
    assert r.status_code == 200 and "sandbox" in r.headers["Content-Security-Policy"]


def test_unknown_artifact_or_version_404(client) -> None:
    assert client.get(_file_url(MULTI, "1-zzzz", "index.html")).status_code == 404
    assert client.get(_file_url("nope", bf.MULTI_ACTIVE, "index.html")).status_code == 404
    assert client.get(_file_url(SINGLE, bf.SINGLE_VERSION["id"], "index.html")).status_code == 404


def test_served_mime_type_is_the_recorded_one(client) -> None:
    export = client.application.config["EXPORT"]
    for url, mime in _all_file_urls(export):
        assert client.get(url).mimetype == mime, url


def test_font_responses_admit_only_opaque_origins(client) -> None:
    r = client.get(_file_url(MULTI, bf.MULTI_ACTIVE, "assets/font.woff2"))
    assert r.headers["Access-Control-Allow-Origin"] == "null"
    assert "Access-Control-Allow-Credentials" not in r.headers
    css = client.get(_file_url(MULTI, bf.MULTI_ACTIVE, "assets/style.css"))
    assert "Access-Control-Allow-Origin" not in css.headers


def test_downloads_write_nothing_under_cache(app_factory, tmp_path: Path) -> None:
    client = app_factory("current").test_client()
    client.get("/")
    before = _snapshot(tmp_path / "cache")
    single = client.get(f"/artifacts/{SINGLE}/v/{bf.SINGLE_VERSION['id']}/download")
    assert single.mimetype == "text/html" and "attachment" in single.headers["Content-Disposition"]
    assert single.data == bf.SINGLE_HTML.encode()
    multi = client.get(f"/artifacts/{MULTI}/v/{bf.MULTI_ACTIVE}/download")
    with zipfile.ZipFile(io.BytesIO(multi.data)) as zf:
        assert sorted(zf.namelist()) == sorted(p for p, _m, _d in bf._multi_version_files("x"))
        assert zf.read("assets/style.css") == bf.MULTI_CSS.encode()
    page = client.get(f"/artifacts/{PAGE}/download")
    assert page.mimetype == "text/markdown" and page.data == bf.PAGE_MARKDOWN.encode()
    assert _snapshot(tmp_path / "cache") == before


def test_inspect_serves_from_memory_and_leaves_cache_identical(
    app_factory, fixtures_dir: Path, tmp_path: Path
) -> None:
    client = app_factory().test_client()  # primary: the legacy fixture
    client.get("/")
    files = [(p.name, p.read_bytes()) for p in sorted((fixtures_dir / "current").glob("*.zip"))]
    before = _snapshot(tmp_path / "cache")
    assert _post_files(client, "/inspect", files).status_code == 302
    for url in (
        f"/artifacts/{MULTI}",
        _file_url(MULTI, bf.MULTI_ACTIVE, "index.html"),
        _file_url(MULTI, bf.MULTI_ACTIVE, "assets/style.css"),
        f"/artifacts/{MULTI}/v/{bf.MULTI_ACTIVE}/download",
        f"/artifacts/{PAGE}/download",
        "/scheduled-tasks",
    ):
        assert client.get(url).status_code == 200, url
    assert _snapshot(tmp_path / "cache") == before


# ---------------------------------------------------------------------------
# §6.2 — nav and pages
# ---------------------------------------------------------------------------


def test_nav_entries_show_only_with_their_categories(app_factory) -> None:
    legacy = app_factory().test_client().get("/").get_data(as_text=True)
    assert ">Artifacts</a>" not in legacy and ">Scheduled tasks</a>" not in legacy
    current = app_factory("current").test_client().get("/").get_data(as_text=True)
    nav = re.search(r'<ul class="nav-links">(.*?)</ul>', current, re.S).group(1)
    labels = re.findall(r">([^<>]+)</a>", nav)
    assert labels.index("Artifacts") == labels.index("Memory") + 1
    assert labels.index("Scheduled tasks") == labels.index("Artifacts") + 1


def test_pages_404_without_their_categories(app_factory) -> None:
    client = app_factory().test_client()
    assert client.get("/artifacts").status_code == 404
    assert client.get("/scheduled-tasks").status_code == 404


def test_artifact_list_copy_and_sorts(client) -> None:
    text = _text(client.get("/artifacts").get_data(as_text=True))
    assert "Published artifacts" in text
    assert "Title Type Versions Updated" in text
    assert "Recently updated Recently created Title (A–Z)" in text
    assert "Synthetic reading log Artifact 3" in text
    assert "Synthetic shared notes Document 1" in text
    titles = re.findall(
        r'<td><a href="/artifacts/[^"]+">([^<]+)</a></td>',
        client.get("/artifacts?sort=title_asc").get_data(as_text=True),
    )
    assert titles == sorted(titles, key=str.lower)
    assert "Saved data without a published artifact" in text
    assert "Collection: notes" in text


def test_unknown_type_and_visibility_show_raw(edge_client) -> None:
    text = _text(
        edge_client.get(f"/artifacts/{bf.ARTIFACT_UNKNOWN_KIND_UUID}").get_data(as_text=True)
    )
    assert "widget · link_shared" in text


def test_detail_sections_in_record_order(client) -> None:
    body = client.get(f"/artifacts/{MULTI}").get_data(as_text=True)
    text = _text(body)
    order = [
        "Synthetic reading log",
        "Artifact · Private",
        "Second draft — the active one.",
        "Version",
        "Preview",
        "Files",
        "Content files",
        "Saved data",
        "Mentioned in conversations",
    ]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    assert "Active version" in text and "Created " in text
    assert "This page uses scripts, which the viewer doesn't run" in text
    assert "Collection: log_entries" in text
    assert "Path Type Size Check" in text and text.count(" OK") >= 6


def test_version_picker_selects_without_loading_others(client) -> None:
    body = client.get(f"/artifacts/{MULTI}?version=1789000000-a7b8").get_data(as_text=True)
    assert "First draft." in _text(body)
    assert re.search(r'<iframe[^>]+src="/artifacts/[^"]+/v/1789000000-a7b8/f/index.html"', body)
    assert len(re.findall(r"<iframe\b", body)) == 1
    assert client.get(f"/artifacts/{MULTI}?version=0-0000").status_code == 404


def test_single_html_notices_and_view_source(client) -> None:
    body = client.get(f"/artifacts/{SINGLE}").get_data(as_text=True)
    text = _text(body)
    assert "Fonts and other resources from the web are blocked" in text
    assert "This page uses scripts" not in text
    # Short source renders open under the View source label (A5's rule).
    assert "<h3>View source</h3>" in body
    assert '<code class="language-html">&lt;!doctype html&gt;' in body
    assert "Download" in text


def test_long_view_source_collapses_by_a5(app_factory, monkeypatch) -> None:
    from viewer import render

    monkeypatch.setattr(render, "DISPLAY_BODY_COLLAPSE_LINES", 5)
    body = app_factory("current").test_client().get(f"/artifacts/{SINGLE}").get_data(as_text=True)
    assert re.search(
        r'<details class="collapsible artifact-source">\s*<summary>View source \(\d+ lines\)',
        body,
    )


def test_files_table_check_values(edge_client) -> None:
    text = _text(edge_client.get(f"/artifacts/{bf.ARTIFACT_INTEGRITY_UUID}").get_data(as_text=True))
    assert "assets/style.css text/css" in text and "Doesn't match the export's record" in text
    assert "content/about.md text/markdown" in text and "Missing from the export" in text
    assert "index.html text/html" in text and " OK" in text


def test_content_files_expand_through_markdown(client) -> None:
    body = client.get(f"/artifacts/{MULTI}").get_data(as_text=True)
    assert re.search(
        r'<details class="collapsible artifact-content-file">\s*'
        r"<summary><code>content/entries.md</code>",
        body,
    )
    assert "<h2>About</h2>" in body


def test_page_kind_renders_markdown_and_comments(client) -> None:
    body = client.get(f"/artifacts/{PAGE}").get_data(as_text=True)
    text = _text(body)
    assert "<iframe" not in body
    assert "<h1>Synthetic shared notes</h1>" in body
    assert "<strong>emphasis</strong>" in body
    assert "Comments" in text
    assert "On “First point” Resolved" in text
    assert "On “Second point”" in text and "On “Second point” Resolved" not in text


def test_empty_states(edge_client) -> None:
    empty = "This version has no files in the export."
    for aid in (bf.ARTIFACT_NO_VERSIONS_UUID, bf.ARTIFACT_ACTIVE_MISSING_UUID):
        assert empty in _text(edge_client.get(f"/artifacts/{aid}").get_data(as_text=True))
    page = edge_client.get(f"/artifacts/{bf.ARTIFACT_BAD_COMMENTS_UUID}").get_data(as_text=True)
    assert empty in _text(page)
    assert "<h2>Comments</h2>" not in page  # unreadable comments: section omitted


def test_mentions_and_empty_mentions(client) -> None:
    multi = client.get(f"/artifacts/{MULTI}").get_data(as_text=True)
    assert f'href="/conversations/{CONV_ARTIFACT}"' in multi
    single = _text(client.get(f"/artifacts/{SINGLE}").get_data(as_text=True))
    assert "Not mentioned in any conversation in this export." in single


def test_tool_card_links_to_artifacts(client) -> None:
    body = client.get(f"/conversations/{CONV_ARTIFACT}").get_data(as_text=True)
    links = re.findall(
        r'<a class="block-card-artifact-link" href="([^"]+)">Open in Artifacts</a>', body
    )
    assert f"/artifacts/{MULTI}" in links and f"/artifacts/{PAGE}" in links


def test_tool_card_link_absent_when_artifact_not_loaded(app_factory) -> None:
    body = (
        app_factory("current_two_part")
        .test_client()
        .get(f"/conversations/{CONV_ARTIFACT}")
        .get_data(as_text=True)
    )
    assert "Open in Artifacts" not in body


def test_preview_print_placeholder(client) -> None:
    body = client.get(f"/artifacts/{SINGLE}").get_data(as_text=True)
    assert '<p class="artifact-preview-print">Preview not printed.</p>' in body
    css = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(
        encoding="utf-8"
    )
    print_block = css[css.index("@media print") :]
    assert ".artifact-preview," in print_block and ".artifact-preview-print {" in print_block


def test_new_surfaces_use_palette_tokens_only() -> None:
    css = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(
        encoding="utf-8"
    )
    block = css[
        css.index("/* v1.4.0 Artifacts and Scheduled tasks") : css.index(".task-prompt-row td")
    ]
    assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S)


# ---------------------------------------------------------------------------
# D9 — Scheduled tasks
# ---------------------------------------------------------------------------


def test_scheduled_tasks_page_copy(client) -> None:
    body = client.get("/scheduled-tasks").get_data(as_text=True)
    text = _text(body)
    assert "Scheduled tasks" in text
    assert "Name Schedule Enabled Created Last run" in text
    assert "Synthetic weekly tidy-up Repeats: 0 9 * * 1 Yes" in text
    assert re.search(r"Synthetic one-off check Once: 2026-09-20 .*? No 2026-09-15 .*? Never", text)
    assert "The prompt was withheld from the export." in text
    assert "Run history" in text and "Scheduled for Started Finished Status Session" in text
    assert "Runs for tasks not in this export" in text
    assert bf.ROUTINE_ABSENT_ID in text
    assert f'id="task-{bf.ROUTINE_CRON_ID}"' in body
    assert "<summary>Prompt</summary>" in body


def test_search_hits_link_to_artifacts_and_tasks(client) -> None:
    body = client.get("/search?q=colour+card").get_data(as_text=True)
    assert f'href="/artifacts/{SINGLE}"' in body and "<h2>Published artifacts</h2>" in body
    body = client.get("/search?q=repository").get_data(as_text=True)
    assert f'href="/scheduled-tasks#task-{bf.ROUTINE_CRON_ID}"' in body
