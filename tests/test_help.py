"""Help page (v1.1 Session C).

Covers the C5 test matrix from the session brief:

- ``/help`` returns 200 and the nav link is present on every page.
- Section structure: top-level ``##`` sections render as default-collapsed
  ``<details>``, ``###`` sub-sections render inside them default-expanded,
  the page-level expand/collapse-all controls are present, and every section
  anchor from the source markdown resolves to an id on the page (the
  anchor-aware expansion JS in ``static/memory.js`` opens them on deep link).
- Internal links: every app-relative href in the rendered page resolves
  without a 404.
- Verify-negatives: the rendered page carries no Windows drive-letter paths,
  no "Dropbox", and no machine usernames — guarding the public-trajectory
  rule at the template level. The app fixture pins ``source_paths`` to a
  bare filename because the footer echoes it verbatim, and an absolute
  ``tmp_path``/repo path would trip the negative checks for reasons unrelated
  to the help template.

Plus unit coverage for :func:`viewer.render.split_help_page`.
"""

from __future__ import annotations

import dataclasses
import getpass
import re
from pathlib import Path

import pytest

from app import create_app
from viewer.parser import load_export
from viewer.render import split_help_page
from viewer.routes import HELP_CONTENT_PATH

# Every GET page in the app — the nav (and so the Help link) must be present
# on each of them.
ALL_PAGES = (
    "/",
    "/conversations",
    "/projects",
    "/memory",
    "/search",
    "/upload",
    "/inspect",
    "/history",
    "/orphans",
    "/help",
)


@pytest.fixture
def synthetic_client(tmp_path: Path, fixtures_dir: Path):
    export = load_export(fixtures_dir / "synthetic_export.zip")
    # Footer echoes source_paths verbatim; pin it to a bare filename so the
    # verify-negative checks below exercise the help template, not the
    # absolute path of wherever this test run happens to live.
    export = dataclasses.replace(export, source_paths=["synthetic_export.zip"])
    app = create_app(export, db_path=tmp_path / "viewer.sqlite")
    return app.test_client()


@pytest.fixture
def help_body(synthetic_client) -> str:
    r = synthetic_client.get("/help")
    assert r.status_code == 200
    return r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Route and navigation (C1)
# ---------------------------------------------------------------------------


def test_help_returns_200(synthetic_client) -> None:
    assert synthetic_client.get("/help").status_code == 200


@pytest.mark.parametrize("page", ALL_PAGES)
def test_nav_link_present_on_every_page(synthetic_client, page: str) -> None:
    r = synthetic_client.get(page)
    assert r.status_code == 200
    assert 'href="/help"' in r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Section structure (C3)
# ---------------------------------------------------------------------------


def test_intro_renders_uncollapsed(help_body: str) -> None:
    assert '<div class="markdown-body help-intro">' in help_body
    # The intro's first sentence is in the page, outside any <details>.
    intro_pos = help_body.index("local viewer for your Claude data export")
    first_details = help_body.index("<details")
    assert intro_pos < first_details


def test_top_level_sections_render_collapsed(help_body: str) -> None:
    blocks = re.findall(r'<details class="memory-block help-block"[^>]*>', help_body)
    assert len(blocks) >= 10  # the copy has 12 top-level sections
    for tag in blocks:
        assert " open" not in tag


def test_subsections_render_open_inside_parents(help_body: str) -> None:
    # Sub-collapsibles are expanded by default within their parent.
    subs = re.findall(r'<details class="memory-section" open[^>]*>', help_body)
    assert len(subs) >= 8  # the copy has 15 sub-sections
    # Spot-check one known nesting: "The three states" inside the tagging
    # section, carrying the parent-scoped anchor id.
    assert 'id="tagging-and-the-triage-workflow--the-three-states"' in help_body
    sec_pos = help_body.index('id="tagging-and-the-triage-workflow"')
    sub_pos = help_body.index('id="tagging-and-the-triage-workflow--the-three-states"')
    assert sec_pos < sub_pos


def test_expand_collapse_controls_present(help_body: str) -> None:
    assert 'class="help-section-toggle"' in help_body
    assert 'data-action="expand"' in help_body
    assert 'data-action="collapse"' in help_body


def test_every_source_heading_has_a_resolving_anchor(help_body: str) -> None:
    """Each ``##``/``###`` in help.md gets a stable id on the page; the
    anchor-expansion JS (loaded on every page via base.html) opens the
    targeted <details> chain on deep links like /help#<slug>."""
    _, _, sections = split_help_page(HELP_CONTENT_PATH.read_text(encoding="utf-8"))
    assert sections, "help.md produced no sections"
    for sec in sections:
        assert f'id="{sec["slug"]}"' in help_body
        for sub in sec["subsections"]:
            assert f'id="{sec["slug"]}--{sub["slug"]}"' in help_body
    # The brief's canonical example anchor.
    assert 'id="tagging-and-the-triage-workflow"' in help_body
    # The expansion handler ships on the page.
    assert "memory.js" in help_body


# ---------------------------------------------------------------------------
# Internal links (C5)
# ---------------------------------------------------------------------------


def test_internal_links_resolve(synthetic_client, help_body: str) -> None:
    hrefs = set(re.findall(r'href="(/[^"]*)"', help_body))
    assert any(h.startswith("/conversations?tag=untagged") for h in hrefs)
    assert "/history" in hrefs
    assert "/search" in hrefs
    for href in sorted(hrefs):
        r = synthetic_client.get(href)
        assert r.status_code < 400, f"{href} returned {r.status_code}"


# ---------------------------------------------------------------------------
# Verify-negatives: public-trajectory guard (C5)
# ---------------------------------------------------------------------------


def test_no_windows_drive_letter_paths(help_body: str) -> None:
    assert not re.search(r"[A-Za-z]:\\", help_body)


def test_no_dropbox_mention(help_body: str) -> None:
    assert "dropbox" not in help_body.lower()


def test_no_machine_username(help_body: str) -> None:
    # Checked dynamically so nothing personal is committed in this file, and
    # the guard still works on any contributor's machine.
    username = getpass.getuser()
    if len(username) >= 4:  # too-short names false-positive on ordinary words
        assert username.lower() not in help_body.lower()


# ---------------------------------------------------------------------------
# split_help_page unit coverage
# ---------------------------------------------------------------------------


def test_split_help_page_structure() -> None:
    text = (
        "# Title\n\nIntro one.\n\nIntro two.\n\n"
        "## First section\n\nSection preamble.\n\n"
        "### Sub one\n\nSub content.\n\n"
        "### Sub two\n\nMore content.\n\n"
        "## Second section\n\nNo subs here.\n"
    )
    title, intro, sections = split_help_page(text)
    assert title == "Title"
    assert intro == "Intro one.\n\nIntro two."
    assert [s["title"] for s in sections] == ["First section", "Second section"]
    first = sections[0]
    assert first["slug"] == "first-section"
    assert first["preamble"] == "Section preamble."
    assert [(s["title"], s["slug"]) for s in first["subsections"]] == [
        ("Sub one", "sub-one"),
        ("Sub two", "sub-two"),
    ]
    assert first["subsections"][0]["content"] == "Sub content."
    assert sections[1]["preamble"] == "No subs here."
    assert sections[1]["subsections"] == []


def test_split_help_page_ignores_headings_in_fences() -> None:
    text = "# T\n\n## Real\n\n```\n## not a heading\n### nor this\n```\nafter\n"
    _, _, sections = split_help_page(text)
    assert [s["title"] for s in sections] == ["Real"]
    assert "## not a heading" in sections[0]["preamble"]


def test_split_help_page_deeper_headings_stay_in_content() -> None:
    text = "## Sec\n\n#### Deep heading\n\nbody\n"
    _, _, sections = split_help_page(text)
    assert sections[0]["subsections"] == []
    assert "#### Deep heading" in sections[0]["preamble"]


def test_split_help_page_handles_empty_input() -> None:
    assert split_help_page("") == ("", "", [])
    assert split_help_page(None) == ("", "", [])


def test_split_help_page_deduplicates_section_slugs() -> None:
    text = "## Same name\n\na\n\n## Same name\n\nb\n"
    _, _, sections = split_help_page(text)
    assert [s["slug"] for s in sections] == ["same-name", "same-name-2"]


def test_shipped_help_md_parses_to_expected_shape() -> None:
    title, intro, sections = split_help_page(HELP_CONTENT_PATH.read_text(encoding="utf-8"))
    assert title == "Help"
    assert "Everything runs locally" in intro
    slugs = [s["slug"] for s in sections]
    assert "tagging-and-the-triage-workflow" in slugs
    assert "troubleshooting" in slugs
    assert len(slugs) == len(set(slugs))
