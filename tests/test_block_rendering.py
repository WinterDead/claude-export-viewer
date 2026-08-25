"""Tier 1 block rendering (v1.3.0 Session S Phase 4, record §9, copy C5/C10).

All six ratified behaviours, rendered from the current-format fixture
through the reading view. The sanitiser-allowlist hard stop held: tables
and cards are template-built, and Markdown rendering reuses the existing
pipeline — nothing here touched the allowlist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import create_app

CONV_MINIMAL = "00000000-0000-0000-0000-0000000000c1"
CONV_TOOLS = "00000000-0000-0000-0000-0000000000d1"
CONV_ARTIFACT = "00000000-0000-0000-0000-0000000000e1"

C5_TOOLTIP = (
    "This thinking block is marked hidden in the Claude app; "
    "the viewer shows everything in your export."
)


@pytest.fixture
def client(fixtures_dir: Path, tmp_path: Path):
    app = create_app(fixtures_dir / "current", db_path=tmp_path / "viewer.sqlite")
    return app.test_client()


@pytest.fixture
def tools_page(client) -> str:
    r = client.get(f"/conversations/{CONV_TOOLS}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# §9.1 — injected_prompt_block: labelled collapsible card, collapsed
# ---------------------------------------------------------------------------


def test_injected_prompt_renders_as_collapsed_card(client) -> None:
    body = client.get(f"/conversations/{CONV_MINIMAL}").get_data(as_text=True)
    m = re.search(r'<details class="injected-prompt"[^>]*>', body)
    assert m, "injected prompt card missing"
    assert " open" not in m.group(0)
    assert "<summary>Injected prompt</summary>" in body
    assert "Synthetic injected instruction text from a product feature." in body
    # No unknown-block placeholder for the now-typed block.
    assert "block-unknown" not in body


# ---------------------------------------------------------------------------
# §9.2 — thinking summaries: first as teaser, full list when expanded
# ---------------------------------------------------------------------------


def test_thinking_summary_is_the_collapsed_teaser(tools_page: str) -> None:
    summaries = re.findall(r"<summary>\s*(.*?)\s*</summary>", tools_page, re.S)
    assert any(s.startswith("Considered what the request needs.") for s in summaries)
    # A thinking block without summaries keeps the char-count teaser.
    assert any(s.startswith("Thinking (") for s in summaries)


def test_thinking_summary_list_at_top_of_expanded_block(tools_page: str) -> None:
    m = re.search(r'<ul class="thinking-summaries">(.*?)</ul>', tools_page, re.S)
    assert m, "expanded summary list missing"
    assert "Considered what the request needs." in m.group(1)
    assert "Planned a web search." in m.group(1)
    # The list precedes the thinking body inside the block.
    assert tools_page.index('class="thinking-summaries"') < tools_page.index(
        "Reasoning about the request."
    )


# ---------------------------------------------------------------------------
# §9.3 — thinking_hidden badge (copy C5 byte-exact)
# ---------------------------------------------------------------------------


def test_thinking_hidden_badge_and_tooltip(tools_page: str) -> None:
    badges = re.findall(r'<span class="thinking-hidden-badge"[^>]*>([^<]*)</span>', tools_page)
    assert badges == ["Hidden in the app"]  # exactly the one hidden block
    assert C5_TOOLTIP in tools_page
    # The hidden block still renders its thinking text — the viewer shows
    # everything in the export.
    assert "Reasoning the product marked hidden." in tools_page


# ---------------------------------------------------------------------------
# §9.4 — text-block citations as a Sources list (heading C10)
# ---------------------------------------------------------------------------


def test_citations_render_as_sources_list(tools_page: str) -> None:
    m = re.search(r'<div class="text-sources">(.*?)</div>', tools_page, re.S)
    assert m, "Sources list missing"
    assert ">Sources</span>" in m.group(0)
    assert "https://example.invalid/cited-1" in m.group(1)
    assert "https://example.invalid/cited-2" in m.group(1)
    # Deliberately not inline markers: the cited text renders unannotated.
    assert "Here is the answer." in tools_page


def test_uncited_text_has_no_sources_list(client) -> None:
    body = client.get(f"/conversations/{CONV_MINIMAL}").get_data(as_text=True)
    assert "text-sources" not in body


# ---------------------------------------------------------------------------
# §9.5 — display_content as the tool card's body, all six types
# ---------------------------------------------------------------------------


def test_display_content_text(tools_page: str) -> None:
    assert re.search(
        r'<div class="display-content display-content-text">\s*Searched the web\s*</div>',
        tools_page,
    )


def test_display_content_table_is_template_built(tools_page: str) -> None:
    m = re.search(
        r'<div class="display-content display-content-table">.*?</table>', tools_page, re.S
    )
    assert m, "display_content table missing"
    for cell in ("Name", "Value", "Alpha", "1", "Beta", "2"):
        assert f"<td>{cell}</td>" in m.group(0)


def test_display_content_rich_link(tools_page: str) -> None:
    m = re.search(
        r'<div class="display-content display-content-link">(.*?)</div>', tools_page, re.S
    )
    assert m
    assert 'href="https://example.invalid/linked"' in m.group(1)
    assert "Synthetic linked resource" in m.group(1)


def test_display_content_json_block_collapsed_preformatted(tools_page: str) -> None:
    m = re.search(
        r'<details class="display-content display-content-json">\s*<summary>json_block</summary>'
        r"\s*<pre>(.*?)</pre>",
        tools_page,
        re.S,
    )
    assert m, "json_block fallback missing"
    assert "&#34;result&#34;: &#34;ok&#34;" in m.group(1)


def test_display_content_rich_content_collapsed_preformatted(tools_page: str) -> None:
    m = re.search(
        r'<details class="display-content display-content-json">\s*<summary>rich_content'
        r"</summary>\s*<pre>(.*?)</pre>",
        tools_page,
        re.S,
    )
    assert m, "rich_content fallback missing"
    assert "First rich item" in m.group(1)
    assert "https://example.invalid/rich-2" in m.group(1)


def test_display_content_code_block_rides_the_highlighter_scope(client) -> None:
    body = client.get(f"/conversations/{CONV_ARTIFACT}").get_data(as_text=True)
    m = re.search(
        r'<div class="markdown-body display-content display-content-code">'
        r'<pre><code class="language-html">',
        body,
    )
    assert m, "code_block body missing or outside the highlighter scope"
    assert "&lt;!doctype html&gt;" in body  # escaped, never raw


# ---------------------------------------------------------------------------
# §9.6 — output_format_category: md renders through the Markdown pipeline
# ---------------------------------------------------------------------------


def test_md_result_text_renders_as_markdown(tools_page: str) -> None:
    assert "<strong>Bold</strong> markdown result with <code>code</code>" in tools_page


def test_non_md_results_keep_raw_inner_text_out_of_the_page(client) -> None:
    """Inner text of ordinary results stays unrendered (cards summarise) —
    the md behaviour is opt-in by the export's own format hint."""
    body = client.get(f"/conversations/{CONV_TOOLS}").get_data(as_text=True)
    assert "Fetched page body." not in body  # the json_block result's inner text
