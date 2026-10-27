"""Phase 8a fixes from the Session U review (``docs/briefs/v1.4.0-session-u-review.md``).

F1 the app's pages reach no network (A9); F2 export URLs link only when they are web
addresses; F3 partial sets never orphan tags (A11, E2); F4 no cross-origin header on
artifact files (A10, in test_artifact_pages); F5 category labels (E4); F6 the resolved
questions Q2, Q4, Q5.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import zipfile
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

from app import APP_CSP
from tests.fixtures import build_fixtures as bf
from tests.test_upload_sets import _post_files
from viewer.artifact_views import ARTIFACT_CSP
from viewer.parser import Message, TextBlock, ToolResultBlock
from viewer.render import web_url
from viewer.tagging import NO_PROJECT_SENTINEL, all_tags, set_tag

ROOT = Path(__file__).resolve().parent.parent
CONV_1 = "00000000-0000-0000-0000-0000000000c1"
CONV_2 = "00000000-0000-0000-0000-0000000000d1"
PROJECT_A = "00000000-0000-0000-0000-0000000000a1"

E2_CONV = (
    "Orphan checks for conversations are paused because this export doesn't include a "
    "conversations file. Your tags are kept as they are."
)
E2_PROJECT = (
    "Orphan checks for projects are paused because this export doesn't include a projects "
    "file. Your tags are kept as they are."
)


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


# ---------------------------------------------------------------------------
# F1 — every response carries a CSP that permits no remote origin (A9)
# ---------------------------------------------------------------------------

PARAMS = {
    "uuid": CONV_1,
    "artifact_id": bf.ARTIFACT_MULTI_UUID,
    "version_id": bf.MULTI_ACTIVE,
    "subpath": "index.html",
    "filename": "style.css",
}


def _route_urls(app) -> list[tuple[str, str, str]]:
    """(endpoint, method, url) for every rule in the route table — generated, so a
    new route is covered without editing this test."""
    out = []
    with app.test_request_context():
        from flask import url_for

        for rule in app.url_map.iter_rules():
            values = {arg: PARAMS[arg] for arg in rule.arguments}
            method = "GET" if "GET" in rule.methods else "POST"
            out.append((rule.endpoint, method, url_for(rule.endpoint, **values)))
    return out


def test_every_route_carries_a_content_security_policy(app_factory) -> None:
    app = app_factory("current")
    client = app.test_client()
    routes = _route_urls(app)
    assert len(routes) >= 30
    for endpoint, method, url in routes:
        r = client.get(url) if method == "GET" else client.post(url)
        csp = r.headers.get("Content-Security-Policy")
        expected = ARTIFACT_CSP if endpoint == "viewer.artifact_file" else APP_CSP
        assert csp == expected, (endpoint, r.status_code)
    # Error responses too.
    assert client.get("/no-such-page").headers["Content-Security-Policy"] == APP_CSP


def _sources(csp: str) -> dict[str, list[str]]:
    return {p.split()[0]: p.split()[1:] for p in csp.split(";") if p.split()}


@pytest.mark.parametrize("csp", [APP_CSP, ARTIFACT_CSP], ids=["app", "artifact"])
def test_no_policy_permits_a_remote_origin_or_inline_script(csp: str) -> None:
    allowed = {"'none'", "'self'", "data:"}
    for directive, sources in _sources(csp).items():
        for source in sources:
            if directive == "style-src" and csp is ARTIFACT_CSP:
                allowed_here = allowed | {"'unsafe-inline'"}
            else:
                allowed_here = allowed
            assert source in allowed_here, (directive, source)
    d = _sources(csp)
    assert d["script-src"] in (["'self'"], ["'none'"])
    assert "'unsafe-eval'" not in csp and "*" not in csp and "http" not in csp
    assert d["object-src"] == ["'none'"] and d["base-uri"] == ["'none'"]
    assert d["frame-ancestors"] == ["'self'"]


def test_app_policy_outcomes() -> None:
    d = _sources(APP_CSP)
    assert d["form-action"] == ["'self'"]
    assert d["style-src"] == ["'self'"]  # no inline styles: the templates use none
    assert d["img-src"] == ["'self'", "data:"]


def test_templates_carry_no_inline_script_or_style() -> None:
    for path in (ROOT / "templates").glob("*.html"):
        source = re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)
        assert not re.search(r"\son[a-z]+=", source), path.name
        assert not re.search(r'\sstyle="', source), path.name
        assert not re.search(r"<style\b", source), path.name
        for tag in re.findall(r"<script\b[^>]*>", source):
            assert "src=" in tag, (path.name, tag)


def test_remote_markdown_image_survives_but_cannot_load(app_factory) -> None:
    client = app_factory("current").test_client()
    r = client.get(f"/conversations/{CONV_2}")
    body = r.get_data(as_text=True)
    assert f'src="{bf.REMOTE_IMAGE_URL}"' in body  # the sanitiser is unchanged
    img = _sources(r.headers["Content-Security-Policy"])["img-src"]
    assert not any("example.invalid" in s or s.startswith("http") for s in img)


# ---------------------------------------------------------------------------
# F2 — export URLs are links only when they are web addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)",
        "\x01\x02javascript:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "jav\rascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//example.invalid/path",
        "https://",
        "ftp://example.invalid/file",
        "mailto:someone@example.invalid",
        "",
        None,
        42,
    ],
)
def test_non_web_urls_are_not_links(value) -> None:
    assert web_url(value) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://example.invalid/a", "https://example.invalid/a"),
        ("HTTP://example.invalid/b", "HTTP://example.invalid/b"),
        ("  https://example.invalid/c\n", "https://example.invalid/c"),
    ],
)
def test_web_urls_are_links(value, expected) -> None:
    assert web_url(value) == expected


def _render(client, block) -> str:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    msg = Message(
        uuid="00000000-0000-0000-0000-00000000f200",
        sender="assistant",
        parent_uuid=None,
        created_at=now,
        updated_at=now,
        content=[block],
    )
    app = client.application
    with app.test_request_context():
        return app.jinja_env.get_template("_message.html").render(msg=msg)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "//example.invalid"])
def test_rich_link_with_a_non_web_url_renders_as_text(client, url) -> None:
    block = ToolResultBlock(
        tool_use_id="t",
        name="web_fetch",
        is_error=False,
        inner=[],
        display_content={"type": "rich_link", "link": {"title": "Linked title", "url": url}},
    )
    html = _render(client, block)
    m = re.search(r'<div class="display-content display-content-link">(.*?)</div>', html, re.S)
    assert m and "<a" not in m.group(1) and "Linked title" in m.group(1)


def test_citation_urls_link_only_when_web_addresses(client) -> None:
    block = TextBlock(
        text="Cited.",
        citations=[
            {"details": {"url": "javascript:alert(1)"}},
            {"details": {"url": "https://example.invalid/ok"}},
        ],
    )
    html = _render(client, block)
    sources = re.search(r'<ul class="text-sources-list">(.*?)</ul>', html, re.S).group(1)
    assert '<a href="https://example.invalid/ok">' in sources
    assert "<li>javascript:alert(1)</li>" in sources
    assert 'href="javascript' not in sources


# ---------------------------------------------------------------------------
# F3 — partial sets never orphan tags (A11, E2)
# ---------------------------------------------------------------------------


def _tagged_app(app_factory, tmp_path):
    app = app_factory()  # legacy primary: all four core categories
    with sqlite3.connect(tmp_path / "cache" / "viewer.sqlite") as conn:
        set_tag(conn, CONV_1, PROJECT_A)
        set_tag(conn, CONV_2, NO_PROJECT_SENTINEL)
    return app, app.test_client()


def _current(fixtures_dir, *names):
    return [
        (f"{n}-000.zip", (fixtures_dir / "current" / f"{n}-000.zip").read_bytes()) for n in names
    ]


def _tags(tmp_path) -> dict:
    with sqlite3.connect(tmp_path / "cache" / "viewer.sqlite") as conn:
        return all_tags(conn)


def _purge(client):
    token = re.search(
        r'name="csrf_token" value="([^"]+)"', client.get("/orphans").get_data(as_text=True)
    ).group(1)
    return client.post("/orphans/purge", data={"csrf_token": token, "confirm": "yes"})


@pytest.mark.parametrize(
    "names,shown,hidden",
    [
        (("projects", "memories"), E2_CONV, E2_PROJECT),
        (("conversations",), E2_PROJECT, E2_CONV),
        (("frames",), E2_CONV, E2_PROJECT),
    ],
    ids=["no-conversations", "no-projects", "neither"],
)
def test_partial_set_pauses_checks_and_keeps_tags(
    app_factory, tmp_path, fixtures_dir, names, shown, hidden
) -> None:
    app, client = _tagged_app(app_factory, tmp_path)
    before = _tags(tmp_path)
    assert _post_files(client, "/upload", _current(fixtures_dir, *names)).status_code == 302

    home = _text(client.get("/").get_data(as_text=True))
    assert shown in home and hidden not in home
    assert "orphan tag row" not in home  # nothing counted

    orphans = client.get("/orphans").get_data(as_text=True)
    assert shown in _text(orphans)
    assert "orphan-purge-form" not in orphans  # no purge control
    _purge(client)
    assert _tags(tmp_path) == before


def test_full_set_keeps_orphan_checks(app_factory, tmp_path, fixtures_dir) -> None:
    app, client = _tagged_app(app_factory, tmp_path)
    with sqlite3.connect(tmp_path / "cache" / "viewer.sqlite") as conn:
        set_tag(conn, "00000000-0000-0000-0000-00000000dead", PROJECT_A)  # a gone conversation
    files = [(p.name, p.read_bytes()) for p in sorted((fixtures_dir / "current").iterdir())]
    assert _post_files(client, "/upload", files).status_code == 302
    home = _text(client.get("/").get_data(as_text=True))
    assert E2_CONV not in home and E2_PROJECT not in home
    assert "1 orphan tag row" in home
    assert "orphan-purge-form" in client.get("/orphans").get_data(as_text=True)
    _purge(client)
    assert "00000000-0000-0000-0000-00000000dead" not in _tags(tmp_path)
    assert CONV_1 in _tags(tmp_path)


# ---------------------------------------------------------------------------
# F5 — category labels (E4)
# ---------------------------------------------------------------------------


def test_manifest_page_uses_category_labels(app_factory, fixtures_dir) -> None:
    client = app_factory().test_client()
    manifest = next((fixtures_dir / "current").glob("*.json"))
    body = _post_files(client, "/manifest", [("m.json", manifest.read_bytes())]).get_data(
        as_text=True
    )
    cells = re.findall(r"<td><code>[^<]+</code></td>\s*<td>([^<]*)</td>", body)
    assert cells == [
        "account",
        "projects",
        "memories",
        "published artifacts",
        "artifact data",
        "Claude Code",
        "conversations",
    ]


# ---------------------------------------------------------------------------
# F6 — Q2 (Home), Q4 (unlisted files), Q5 (no entry page)
# ---------------------------------------------------------------------------


def test_home_follows_the_memory_page_rules(app_factory) -> None:
    current = app_factory("current").test_client()
    assert "Legacy memory (no longer updated)" in current.get("/").get_data(as_text=True)
    current.set_cookie("viewer_legacy_memory", "hide")
    home = current.get("/").get_data(as_text=True)
    assert 'id="global-memory"' not in home and "Global memory" not in home
    legacy = app_factory().test_client()
    legacy.set_cookie("viewer_legacy_memory", "hide")
    body = legacy.get("/").get_data(as_text=True)
    assert 'id="global-memory"' in body and "Legacy memory" not in body


def test_unlisted_files_are_shown_and_never_served(app_factory) -> None:
    client = app_factory("current_frames_edge").test_client()
    text = _text(client.get(f"/artifacts/{bf.ARTIFACT_INTEGRITY_UUID}").get_data(as_text=True))
    assert "extra/unlisted.txt — — Not in the export's file list (not served)" in text
    url = f"/artifacts/{bf.ARTIFACT_INTEGRITY_UUID}/v/1789200000-0b0b/f/extra/unlisted.txt"
    assert client.get(url).status_code == 404


def test_multi_file_version_without_entry_page(app_factory, tmp_path) -> None:
    vid = "1789000000-0e0e"
    base = f"artifacts/{bf.ARTIFACT_MULTI_UUID}"
    files = [("notes.md", "text/markdown", b"# Notes\n")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{base}/artifact.json",
            json.dumps(
                {
                    "id": bf.ARTIFACT_MULTI_UUID,
                    "kind": "artifact",
                    "visibility": "private",
                    "active_version": vid,
                    "versions": [
                        {
                            "id": vid,
                            "title": "t",
                            "description": "",
                            "created_at": "2026-09-09T00:00:00+00:00",
                        }
                    ],
                }
            ),
        )
        zf.writestr(f"{base}/versions/{vid}.files.json", bf._files_json(files))
        zf.writestr(f"{base}/versions/{vid}/notes.md", files[0][2])
    set_dir = tmp_path / "noentry"
    set_dir.mkdir()
    (set_dir / "frames-000.zip").write_bytes(buf.getvalue())
    from app import create_app

    client = create_app(set_dir, db_path=tmp_path / "n.sqlite").test_client()
    text = _text(client.get(f"/artifacts/{bf.ARTIFACT_MULTI_UUID}").get_data(as_text=True))
    assert "This version has no page to preview. Its files are listed below." in text
    assert "This version has no files in the export." not in text
    assert "notes.md text/markdown" in text


@pytest.fixture
def client(app_factory):
    return app_factory("current").test_client()
