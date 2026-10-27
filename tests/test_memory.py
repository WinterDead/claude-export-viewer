"""Route + template tests for the memory view (plan §12 step 5 + polish).

Covers:

- Happy-path rendering against ``synthetic_export.zip`` (no orphans).
- Orphan-section rendering via ``bad_dangling_memory.zip`` — the
  preserve-and-flag pattern surface.
- Collapsibility: every memory is wrapped in ``<details>`` with no ``open``
  attribute (default-collapsed).
- Sort toggle: ``?sort=`` produces the right project-memory order; global
  memory is always first.
- Per-memory section IDs: anchor IDs match the search-route's link form
  (``memory-<uuid>`` and ``global-memory``) so the JS hash handler can
  open them.
- Per-memory expand/collapse-all toggle is rendered only when sections are
  detected — not on single-block fallback memories.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import create_app
from tests.conftest import DUAL_FORMAT_IDS, DUAL_FORMATS


@pytest.fixture(params=DUAL_FORMATS, ids=DUAL_FORMAT_IDS)
def export_path(request, fixtures_dir: Path) -> Path:
    """Dual-format module (v1.3.0 Session S): export-dependent tests in this
    file run against both container formats."""
    return fixtures_dir / request.param


@pytest.fixture
def dangling_client(tmp_path: Path, fixtures_dir: Path):
    app = create_app(
        fixtures_dir / "bad_dangling_memory.zip",
        db_path=tmp_path / "viewer.sqlite",
    )
    return app.test_client()


# ---------------------------------------------------------------------------
# Happy-path coverage (kept from the original test_memory.py)
# ---------------------------------------------------------------------------


def test_memory_view_renders_global_and_project_memory(client) -> None:
    r = client.get("/memory")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # Record §7 / Appendix C7: the blob's group heading.
    assert "Conversation memory" in body
    # proj-A has memory in the synthetic fixture.
    assert "Synthetic Project A" in body
    # proj-B has no memory; its name should NOT appear in the memory list.
    assert "Synthetic Starter Project B" not in body


def test_memory_view_omits_orphan_section_when_none(client) -> None:
    body = client.get("/memory").get_data(as_text=True)
    assert "Orphan memories" not in body


def test_memory_view_renders_orphan_section_when_present(dangling_client) -> None:
    r = dangling_client.get("/memory")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Orphan memories" in body
    assert "00000000-0000-0000-0000-00000000dead" in body
    # The orphan body content must render — the fixture's body starts with
    # ``**Orphan memory**``, which the section splitter converts into the
    # section's <summary> heading.
    assert "Orphan memory" in body


# ---------------------------------------------------------------------------
# Collapsibility
# ---------------------------------------------------------------------------


def test_each_memory_is_pre_collapsed_details(client) -> None:
    """Every memory block is wrapped in <details> with no ``open`` attribute,
    so the user lands on a compact list and clicks to expand."""
    body = client.get("/memory").get_data(as_text=True)
    # Find the global memory's details element.
    m = re.search(
        r'<details class="memory-block memory-block-global"[^>]*id="global-memory"[^>]*>',
        body,
    )
    assert m, "global memory must be a <details> block"
    assert " open" not in m.group(0)

    # And the project memory.
    m = re.search(
        r'<details class="memory-block memory-block-project"[^>]*'
        r'id="memory-00000000-0000-0000-0000-0000000000a1"[^>]*>',
        body,
    )
    assert m
    assert " open" not in m.group(0)


def test_internal_sections_are_open_by_default(client) -> None:
    """Inside an expanded memory, section <details> are pre-opened."""
    body = client.get("/memory").get_data(as_text=True)
    section_match = re.search(
        r'<details class="memory-section" open[^>]*>',
        body,
    )
    assert section_match


def test_expand_collapse_toggle_present_when_sections_detected(client) -> None:
    """Synthetic global memory has a ``**Work context**`` heading → toggle
    bar appears."""
    body = client.get("/memory").get_data(as_text=True)
    assert 'class="memory-section-toggle"' in body
    assert 'data-action="expand"' in body
    assert 'data-action="collapse"' in body


def test_section_anchor_ids_use_slug_format(client) -> None:
    """Section-level IDs follow ``<memory-anchor>--<slug>`` so JS can walk
    the DOM and any future search hit at section level resolves."""
    body = client.get("/memory").get_data(as_text=True)
    assert 'id="global-memory--work-context"' in body


# ---------------------------------------------------------------------------
# Sort toggle
# ---------------------------------------------------------------------------


def test_memory_sort_toggle_renders_three_options(client) -> None:
    body = client.get("/memory").get_data(as_text=True)
    assert "Recently updated" in body
    assert "Recently created" in body
    assert "Name (A–Z)" in body


def test_memory_default_sort_is_updated_desc(client) -> None:
    body = client.get("/memory").get_data(as_text=True)
    m = re.search(r"sort-option active[^>]*>\s*Recently updated", body)
    assert m


def test_memory_invalid_sort_falls_back_to_default(client) -> None:
    body = client.get("/memory?sort=bogus").get_data(as_text=True)
    m = re.search(r"sort-option active[^>]*>\s*Recently updated", body)
    assert m


def test_memory_global_position_holds_regardless_of_sort(client, export) -> None:
    """Without memory files, the conversation memory's <details> precedes
    every project memory; once the set has memory files the summary is
    legacy and follows them (export-categories record §7) — under every
    sort."""
    for sort in ("updated_desc", "name_asc", "created_desc"):
        body = client.get(f"/memory?sort={sort}").get_data(as_text=True)
        global_pos = body.find('id="global-memory"')
        project_pos = body.find('id="memory-00000000-0000-0000-0000-0000000000a1"')
        assert global_pos != -1
        assert project_pos != -1
        if export.memory_files:
            assert global_pos > project_pos, f"legacy memory must come last under sort={sort}"
        else:
            assert global_pos < project_pos, f"global must precede project under sort={sort}"


def test_memory_name_asc_sort_param_active(client) -> None:
    body = client.get("/memory?sort=name_asc").get_data(as_text=True)
    m = re.search(r"sort-option active[^>]*>\s*Name \(A–Z\)", body)
    assert m


def test_memory_created_desc_sort_param_active(client) -> None:
    body = client.get("/memory?sort=created_desc").get_data(as_text=True)
    m = re.search(r"sort-option active[^>]*>\s*Recently created", body)
    assert m


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------


def test_home_global_memory_pre_collapsed(client) -> None:
    body = client.get("/").get_data(as_text=True)
    m = re.search(
        r'<details class="memory-block memory-block-global"[^>]*id="global-memory"[^>]*>',
        body,
    )
    assert m, "home page must wrap global memory in a <details> block"
    assert " open" not in m.group(0)


def test_home_global_memory_anchor_id_matches_memory_view(client) -> None:
    """Home's global memory and /memory's must use the same anchor id so a
    search hit linked to either resolves."""
    home = client.get("/").get_data(as_text=True)
    mem = client.get("/memory").get_data(as_text=True)
    assert 'id="global-memory"' in home
    assert 'id="global-memory"' in mem


# ---------------------------------------------------------------------------
# memory.js is loaded site-wide
# ---------------------------------------------------------------------------


def test_memory_js_loaded_on_every_page(client) -> None:
    """Anchor expansion is needed on any page that links to a memory anchor.
    Loading site-wide via base.html is the simplest correct thing."""
    for path in ("/", "/memory", "/conversations", "/search"):
        body = client.get(path).get_data(as_text=True)
        assert 'src="/static/memory.js"' in body, f"{path} missing memory.js"


# ---------------------------------------------------------------------------
# Memory files (v1.3.0, record §7 — current format only)
# ---------------------------------------------------------------------------


@pytest.fixture
def current_client(tmp_path: Path, fixtures_dir: Path):
    app = create_app(fixtures_dir / "current", db_path=tmp_path / "viewer-current.sqlite")
    return app.test_client()


def test_memory_files_group_renders_with_c7_copy(current_client) -> None:
    body = current_client.get("/memory").get_data(as_text=True)
    assert "Memory files" in body
    assert "Project memories" in body
    # Every file is a collapsible titled by its full path.
    for path in (
        "/profile.md",
        "/people/synthetic-colleague.md",
        "/topics/nested/deeper-topic.md",
        "/preferences.md",
    ):
        assert path in body
    # Appendix C7 per-file line.
    assert re.search(r"Updated ", body)


def test_memory_files_grouped_by_top_level_segment(current_client) -> None:
    """Open-set grouping: whatever top-level directories exist become
    groups; root-level files come first without a directory heading."""
    body = current_client.get("/memory").get_data(as_text=True)
    assert "<code>/people/</code>" in body
    assert "<code>/topics/</code>" in body
    # Root files precede the directory groups.
    assert body.index("/profile.md") < body.index("<code>/people/</code>")


def test_memory_file_frontmatter_fence_preformatted(current_client) -> None:
    body = current_client.get("/memory").get_data(as_text=True)
    m = re.search(r'<pre class="memory-file-frontmatter">(.*?)</pre>', body, re.S)
    assert m, "frontmatter fence block missing"
    assert "name: profile" in body
    # The body below the fence renders as sanitised Markdown.
    assert "<strong>markdown</strong>" in body


def test_memory_file_without_frontmatter_renders_entirely_as_markdown(current_client) -> None:
    body = current_client.get("/memory").get_data(as_text=True)
    assert "Synthetic preference notes without a frontmatter fence." in body
    # No fence block for that file: exactly three of the four have one.
    fences = re.findall(r'<pre class="memory-file-frontmatter">', body)
    assert len(fences) == 3


def test_memory_files_collapsed_by_default_with_anchor_ids(current_client) -> None:
    body = current_client.get("/memory").get_data(as_text=True)
    m = re.search(r'<details class="memory-block memory-block-file"[^>]*>', body)
    assert m and " open" not in m.group(0)
    assert 'id="memory-file-profile-md"' in body


def test_legacy_export_shows_no_memory_files_group(tmp_path: Path, fixtures_dir: Path) -> None:
    app = create_app(
        fixtures_dir / "synthetic_export.zip", db_path=tmp_path / "viewer-legacy.sqlite"
    )
    body = app.test_client().get("/memory").get_data(as_text=True)
    assert "Memory files" not in body
