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
    m = re.search(r'<details class="collapsible injected-prompt"[^>]*>', body)
    assert m, "injected prompt card missing"
    assert " open" not in m.group(0)
    assert "<summary>Injected prompt</summary>" in body
    assert "Synthetic injected instruction text from a product feature." in body
    # No unknown-block placeholder for the now-typed block.
    assert "block-unknown" not in body


def test_reading_view_collapsibles_share_one_contract(client) -> None:
    """Session T (hand-test scenario 5 follow-up): every reading-view
    <details> — thinking, injected-prompt, display-content fallbacks,
    attachments — sits on the one shared mechanism: a native
    ``<details class="collapsible …">`` whose first child is a ``<summary>``,
    with no ``open`` attribute. One contract, not parallel ones."""
    for conv in (CONV_MINIMAL, CONV_TOOLS):
        body = client.get(f"/conversations/{conv}").get_data(as_text=True)
        tags = re.findall(r"<details[^>]*>", body)
        assert tags, f"no collapsibles rendered for {conv}"
        for tag in tags:
            assert 'class="collapsible ' in tag, f"off-contract details tag: {tag}"
            assert " open" not in tag, f"pre-opened details tag: {tag}"
        # Each details opens with its summary — the toggle every browser wires.
        assert len(re.findall(r"<details[^>]*>\s*<summary", body)) == len(tags)
    # The two kinds the hand-test exercised are both on the contract.
    tools_body = client.get(f"/conversations/{CONV_TOOLS}").get_data(as_text=True)
    minimal_body = client.get(f"/conversations/{CONV_MINIMAL}").get_data(as_text=True)
    assert '<details class="collapsible thinking">' in tools_body
    assert '<details class="collapsible injected-prompt">' in minimal_body


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
        r'<details class="collapsible display-content display-content-json">'
        r"\s*<summary>json_block</summary>"
        r"\s*<pre>(.*?)</pre>",
        tools_page,
        re.S,
    )
    assert m, "json_block fallback missing"
    assert "&#34;result&#34;: &#34;ok&#34;" in m.group(1)


def test_display_content_rich_content_collapsed_preformatted(tools_page: str) -> None:
    m = re.search(
        r'<details class="collapsible display-content display-content-json">'
        r"\s*<summary>rich_content</summary>\s*<pre>(.*?)</pre>",
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


# ---------------------------------------------------------------------------
# Amendment A5 — display-content bodies collapse by size (copy U1)
# ---------------------------------------------------------------------------


def _render_tool_result(client, display_content: dict, *, message: str | None = None) -> str:
    """Render the real ``_message.html`` partial around one tool_result block."""
    from datetime import UTC, datetime

    from viewer.parser import Message, ToolResultBlock

    block = ToolResultBlock(
        tool_use_id="toolu_a5",
        name="view",
        is_error=False,
        inner=[],
        display_content=display_content,
        message=message,
    )
    now = datetime(2026, 9, 1, tzinfo=UTC)
    msg = Message(
        uuid="00000000-0000-0000-0000-00000000a500",
        sender="assistant",
        parent_uuid=None,
        created_at=now,
        updated_at=now,
        content=[block],
    )
    app = client.application
    with app.test_request_context():
        return app.jinja_env.get_template("_message.html").render(msg=msg)


FOLD_RE = re.compile(
    r'<details class="collapsible display-content-fold">\s*<summary>(.*?)</summary>', re.S
)


def test_a5_constants_are_named_in_render() -> None:
    from viewer import render

    assert render.DISPLAY_BODY_COLLAPSE_LINES == 30
    assert render.DISPLAY_BODY_COLLAPSE_CHARS == 2000


def test_a5_body_under_both_thresholds_renders_open(client) -> None:
    text = "\n".join(f"line {n}" for n in range(30))  # exactly 30 lines, short
    html = _render_tool_result(client, {"type": "text", "text": text}, message="Read a file")
    assert not FOLD_RE.search(html)
    assert '<div class="display-content display-content-text">' in html


def test_a5_over_line_threshold_only_collapses_with_teaser(client) -> None:
    text = "\n".join("x" for _ in range(31))  # 31 lines, 61 characters
    html = _render_tool_result(client, {"type": "text", "text": text}, message="Read a file")
    m = FOLD_RE.search(html)
    assert m and m.group(1) == "Read a file (31 lines)"


def test_a5_over_character_threshold_only_collapses_with_teaser(client) -> None:
    code = "\n".join("y" * 200 for _ in range(11))  # 11 lines, 2,210 characters
    html = _render_tool_result(
        client, {"type": "code_block", "code": code, "language": "text"}, message=None
    )
    m = FOLD_RE.search(html)
    # No message line: the tool name teases instead.
    assert m and m.group(1) == "view (11 lines)"
    assert '<div class="markdown-body display-content display-content-code">' in html


def test_a5_over_both_thresholds_collapses_tables_too(client) -> None:
    table = [["cell " * 20, str(n)] for n in range(40)]
    html = _render_tool_result(client, {"type": "table", "table": table}, message="Listed rows")
    m = FOLD_RE.search(html)
    assert m and m.group(1) == "Listed rows (40 lines)"


def test_a5_fold_sits_on_the_shared_collapsible_contract(client) -> None:
    text = "\n".join("z" for _ in range(50))
    html = _render_tool_result(client, {"type": "text", "text": text}, message="Read")
    tag = re.search(r"<details[^>]*>", html).group(0)
    assert tag == '<details class="collapsible display-content-fold">'
    assert " open" not in tag
    assert re.search(r"<details[^>]*>\s*<summary", html)


def test_a5_leaves_json_block_and_rich_content_always_collapsed(client) -> None:
    big = '{"k": "' + "v" * 5000 + '"}'
    html = _render_tool_result(client, {"type": "json_block", "json_block": big}, message="Big")
    assert not FOLD_RE.search(html)
    assert "<summary>json_block</summary>" in html
    html = _render_tool_result(client, {"type": "rich_content", "content": []}, message="Big")
    assert not FOLD_RE.search(html)
    assert "<summary>rich_content</summary>" in html


def test_a5_fixture_skill_file_renders_collapsed_in_the_reading_view(tools_page: str) -> None:
    from tests.fixtures.build_fixtures import SKILL_FILE_LINES, SKILL_FILE_MESSAGE

    teasers = [m.group(1) for m in FOLD_RE.finditer(tools_page)]
    assert f"{SKILL_FILE_MESSAGE} ({SKILL_FILE_LINES} lines)" in teasers
