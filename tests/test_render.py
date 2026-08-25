"""Unit tests for ``viewer.render``.

The templates and routes are not unit-tested (per CLAUDE.md), but the helpers
they depend on are pure functions worth pinning down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import DUAL_FORMAT_IDS, DUAL_FORMATS
from viewer.parser import (
    Conversation,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from viewer.render import (
    block_type,
    compute_render_path,
    conversation_display_title,
    format_local,
    format_local_relative,
    markdown_to_html,
    render_search_snippet,
    slugify,
    split_memory_sections,
    summary_preview,
)
from viewer.search import SENTINEL_CLOSE, SENTINEL_OPEN


@pytest.fixture(params=DUAL_FORMATS, ids=DUAL_FORMAT_IDS)
def export_path(request, fixtures_dir: Path) -> Path:
    """Dual-format module (v1.3.0 Session S): export-dependent tests in this
    file run against both container formats."""
    return fixtures_dir / request.param


# ---------------------------------------------------------------------------
# markdown_to_html
# ---------------------------------------------------------------------------


def test_markdown_renders_paragraphs() -> None:
    out = str(markdown_to_html("Hello **world**"))
    assert "<p>" in out
    assert "<strong>world</strong>" in out


def test_markdown_renders_fenced_code() -> None:
    src = "```python\nprint('hi')\n```"
    out = str(markdown_to_html(src))
    assert "<pre>" in out
    assert "<code" in out
    assert "print" in out


def test_markdown_renders_tables() -> None:
    src = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    out = str(markdown_to_html(src))
    assert "<table>" in out
    assert "<th>a</th>" in out


def test_markdown_handles_none_and_empty() -> None:
    assert str(markdown_to_html(None)) == ""
    assert str(markdown_to_html("")) == ""


def test_markdown_returns_markup_so_jinja_does_not_re_escape() -> None:
    from markupsafe import Markup

    out = markdown_to_html("hi")
    assert isinstance(out, Markup)


# ---------------------------------------------------------------------------
# Sanitisation — bleach must strip dangerous tags/attributes/protocols.
# ---------------------------------------------------------------------------


def test_sanitiser_strips_script_tag() -> None:
    out = str(markdown_to_html("Hello <script>alert(1)</script> world"))
    assert "<script" not in out
    assert "</script" not in out
    # Inner text is preserved; what matters is the tag is gone.
    assert "alert(1)" in out


def test_sanitiser_strips_iframe() -> None:
    out = str(markdown_to_html('Before <iframe src="https://evil.invalid"></iframe> after'))
    assert "<iframe" not in out
    assert "Before" in out
    assert "after" in out


def test_sanitiser_strips_event_handlers() -> None:
    out = str(markdown_to_html('<img src="x.png" onerror="alert(1)" alt="x">'))
    assert "<img" in out  # the tag itself is on the allowlist
    assert "onerror" not in out
    assert "alert(1)" not in out


def test_sanitiser_drops_javascript_href() -> None:
    out = str(markdown_to_html("[click me](javascript:alert(1))"))
    # The anchor tag is allowed, but bleach drops the javascript: href.
    assert "javascript:" not in out
    # Visible text still shows up.
    assert "click me" in out


def test_sanitiser_drops_data_url_href() -> None:
    """``data:`` URLs are the more interesting modern attack vector — they can
    carry inline HTML/JS. Bleach's default protocol allowlist excludes them."""
    out = str(markdown_to_html("[click](data:text/plain,benign)"))
    assert "data:" not in out
    assert "click" in out

    # Same for a payload that would be dangerous if it survived.
    out = str(markdown_to_html("[click](data:text/html,<b>hi</b>)"))
    assert "data:" not in out


def test_sanitiser_keeps_http_and_https_hrefs() -> None:
    out = str(markdown_to_html("[example](https://example.invalid/path)"))
    assert 'href="https://example.invalid/path"' in out


def test_sanitiser_preserves_html_inside_fenced_code() -> None:
    """HTML in code blocks is rendered as escaped text — that's preserved."""
    src = "```html\n<script>x</script>\n```"
    out = str(markdown_to_html(src))
    # The literal characters appear as escaped entities inside <code>; the
    # important thing is no live <script> tag survives.
    assert "<script>x" not in out
    assert "&lt;script&gt;" in out


def test_sanitiser_preserves_markdown_emphasis() -> None:
    out = str(markdown_to_html("**bold** and *italic*"))
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out


def test_sanitiser_preserves_fenced_code_language_class() -> None:
    out = str(markdown_to_html("```python\nprint('hi')\n```"))
    # python-markdown emits <code class="language-python"> inside <pre>.
    assert 'class="language-python"' in out


# ---------------------------------------------------------------------------
# The ``class`` boundary (v1.1 Session F). ``class`` survives only on <code>
# and only when the ENTIRE value matches ^language-[A-Za-z0-9_+#.-]+$ — the
# fenced-code language hint the client-side highlighter reads. Everything
# below is a verify-negative pinning that boundary; the verification matrix
# lives in docs/briefs/v1.1-session-f-verification.md. Do not loosen these
# assertions without a written brief.
# ---------------------------------------------------------------------------


def test_code_class_language_python_survives() -> None:
    out = str(markdown_to_html('<code class="language-python">x</code>'))
    assert 'class="language-python"' in out


def test_code_class_special_character_languages_survive() -> None:
    """The pattern's ``+#.-`` exists for real language names."""
    for lang in ("c++", "c#", "objective-c"):
        out = str(markdown_to_html(f'<code class="language-{lang}">x</code>'))
        assert f'class="language-{lang}"' in out, lang


def test_code_class_fenced_path_special_characters_survive() -> None:
    """Same languages via the real-world path — a fenced block with a hint."""
    out = str(markdown_to_html("```c++\nint x;\n```"))
    assert 'class="language-c++"' in out


def test_code_class_multi_class_value_stripped() -> None:
    """The ENTIRE value must match — a multi-class value is stripped outright,
    even though one of its classes would match alone."""
    out = str(markdown_to_html('<code class="evil language-python">x</code>'))
    assert "class=" not in out
    assert "<code>" in out  # the tag itself survives, bare


def test_code_class_non_language_value_stripped() -> None:
    out = str(markdown_to_html('<code class="anything-else">x</code>'))
    assert "class=" not in out


def test_class_on_pre_stripped() -> None:
    out = str(markdown_to_html('<pre class="fancy"><code class="language-python">x</code></pre>'))
    assert "<pre>" in out  # tag survives...
    assert 'class="fancy"' not in out  # ...its class does not
    assert 'class="language-python"' in out  # the <code> hint inside still does


def test_class_on_span_and_div_stripped() -> None:
    """span/div aren't on the tag allowlist at all — the whole tag goes
    (inner text kept), so no class can survive on them by construction."""
    out = str(markdown_to_html('<span class="x">keep me</span>'))
    assert "<span" not in out
    assert "keep me" in out

    out = str(markdown_to_html('<div class="x">keep me too</div>'))
    assert "<div" not in out
    assert "keep me too" in out


def test_class_on_anchor_stripped() -> None:
    out = str(markdown_to_html('<a class="x" href="https://example.invalid/">link</a>'))
    assert "<a" in out
    assert 'href="https://example.invalid/"' in out
    assert "class=" not in out


def test_style_attribute_stripped_everywhere() -> None:
    """``style`` is never allowed — not even on a <code> with a valid hint."""
    out = str(markdown_to_html('<code class="language-python" style="color:red">x</code>'))
    assert "style=" not in out
    assert 'class="language-python"' in out

    out = str(markdown_to_html('<p style="position:fixed">x</p>'))
    assert "style=" not in out


def test_event_handler_on_code_stripped_class_kept() -> None:
    out = str(markdown_to_html('<code class="language-python" onclick="alert(1)">x</code>'))
    assert "onclick" not in out
    assert 'class="language-python"' in out


def test_sanitiser_keeps_mark_tag() -> None:
    """<mark> is on the allowlist for search highlighting (plan §12 step 6).

    The render layer's snippet substitution happens after bleach so this
    allowlist entry is defence-in-depth — but if a future markdown blob
    legitimately contains ``<mark>``, we want it to survive."""
    out = str(markdown_to_html("Before <mark>highlight</mark> after"))
    assert "<mark>" in out
    assert "highlight" in out


# ---------------------------------------------------------------------------
# render_search_snippet — sentinel-to-<mark> substitution post-bleach.
# ---------------------------------------------------------------------------


def test_search_snippet_substitutes_sentinels() -> None:
    raw = f"foo {SENTINEL_OPEN}match{SENTINEL_CLOSE} bar"
    out = str(render_search_snippet(raw))
    assert "<mark>match</mark>" in out
    assert SENTINEL_OPEN not in out
    assert SENTINEL_CLOSE not in out


def test_search_snippet_handles_empty() -> None:
    assert str(render_search_snippet(None)) == ""
    assert str(render_search_snippet("")) == ""


def test_search_snippet_returns_markup() -> None:
    from markupsafe import Markup

    out = render_search_snippet("plain")
    assert isinstance(out, Markup)


def test_search_snippet_strips_dangerous_html_inside_match() -> None:
    """Bleach runs before the substitution, so a <script> tag inside the
    snippet body still gets stripped even though the sentinels survive."""
    raw = f"{SENTINEL_OPEN}<script>x</script>{SENTINEL_CLOSE}"
    out = str(render_search_snippet(raw))
    assert "<script" not in out
    assert "<mark>" in out


# ---------------------------------------------------------------------------
# format_local
# ---------------------------------------------------------------------------


def test_format_local_aware_datetime_renders_in_some_zone() -> None:
    ts = datetime(2026, 4, 5, 17, 11, 30, tzinfo=UTC)
    out = format_local(ts)
    # We do not pin the system zone, so just assert structural shape.
    assert out.startswith("2026-")
    assert ":" in out
    # strftime("%Z") may be empty on systems without tz names, but it should
    # at least produce a date-time prefix in any case.


def test_format_local_naive_treated_as_utc() -> None:
    aware = datetime(2026, 4, 5, 17, 11, 30, tzinfo=UTC)
    naive = datetime(2026, 4, 5, 17, 11, 30)  # same wall clock
    assert format_local(aware) == format_local(naive)


def test_format_local_none() -> None:
    assert format_local(None) == "—"


def test_format_local_normalises_non_utc_aware() -> None:
    # 17:11 UTC == 13:11 EDT (UTC-4) — both should produce identical output.
    eastern = timezone(timedelta(hours=-4))
    aware_utc = datetime(2026, 4, 5, 17, 11, 30, tzinfo=UTC)
    aware_edt = datetime(2026, 4, 5, 13, 11, 30, tzinfo=eastern)
    assert format_local(aware_utc) == format_local(aware_edt)


# ---------------------------------------------------------------------------
# format_local_relative
# ---------------------------------------------------------------------------


def test_relative_just_now() -> None:
    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    assert format_local_relative(now, now=now) == "just now"
    assert format_local_relative(now - timedelta(seconds=10), now=now) == "just now"


def test_relative_minutes() -> None:
    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    assert format_local_relative(now - timedelta(minutes=5), now=now) == "5 minutes ago"
    assert format_local_relative(now - timedelta(minutes=1), now=now) == "1 minute ago"


def test_relative_hours_and_days() -> None:
    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    assert format_local_relative(now - timedelta(hours=3), now=now) == "3 hours ago"
    assert format_local_relative(now - timedelta(days=2), now=now) == "2 days ago"


def test_relative_future() -> None:
    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    assert format_local_relative(now + timedelta(hours=2), now=now) == "in 2 hours"
    assert format_local_relative(now + timedelta(seconds=10), now=now) == "in a moment"


def test_relative_naive_treated_as_utc() -> None:
    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    naive_past = datetime(2026, 4, 5, 11, 0, 0)
    assert format_local_relative(naive_past, now=now) == "1 hour ago"


def test_relative_none() -> None:
    assert format_local_relative(None) == "—"


# ---------------------------------------------------------------------------
# conversation_display_title — fallback chain per plan §12 step 2.
# ---------------------------------------------------------------------------


def _make_conv(
    *, title: str = "", messages: list[Message] | None = None, created: datetime | None = None
) -> Conversation:
    """Build a frozen ``Conversation`` for fallback-title tests."""
    ts = created or datetime(2026, 4, 5, 10, 0, 0, tzinfo=UTC)
    return Conversation(
        uuid="00000000-0000-0000-0000-000000000001",
        title=title,
        summary="",
        created_at=ts,
        updated_at=ts,
        messages=messages or [],
        children_by_uuid={},
        canonical_path=[],
        branch_points=[],
    )


def _human_msg(text: str) -> Message:
    ts = datetime(2026, 4, 5, 10, 0, 0, tzinfo=UTC)
    return Message(
        uuid="msg-1",
        sender="human",
        parent_uuid=None,
        created_at=ts,
        updated_at=ts,
        content=[TextBlock(text=text)] if text else [],
    )


def test_title_fallback_uses_real_title_when_present() -> None:
    conv = _make_conv(title="A real title")
    assert conversation_display_title(conv) == "A real title"


def test_title_fallback_treats_whitespace_as_empty() -> None:
    conv = _make_conv(title="   \n  ", messages=[_human_msg("the human said something")])
    out = conversation_display_title(conv)
    assert out.startswith("Untitled conversation —")
    assert "the human said something" in out


def test_title_fallback_uses_first_human_message_preview() -> None:
    conv = _make_conv(messages=[_human_msg("Hello, can you help me?")])
    assert conversation_display_title(conv) == "Untitled conversation — Hello, can you help me?"


def test_title_fallback_truncates_long_preview() -> None:
    long_text = "x" * 200
    conv = _make_conv(messages=[_human_msg(long_text)])
    out = conversation_display_title(conv)
    assert out.endswith("…")
    # 80-char cap on the preview portion (after "Untitled conversation — ").
    preview = out.removeprefix("Untitled conversation — ").removesuffix("…")
    assert len(preview) == 80


def test_title_fallback_does_not_truncate_short_preview() -> None:
    conv = _make_conv(messages=[_human_msg("short")])
    out = conversation_display_title(conv)
    assert "…" not in out
    assert out == "Untitled conversation — short"


def test_title_fallback_falls_through_to_date_when_no_messages() -> None:
    conv = _make_conv(created=datetime(2026, 4, 5, 10, 0, 0, tzinfo=UTC))
    out = conversation_display_title(conv)
    assert out.startswith("Untitled conversation · ")
    # Date is the local-zone rendering of 2026-04-05 UTC; just spot-check year.
    assert "2026" in out


def test_title_fallback_falls_through_when_first_human_text_is_empty() -> None:
    conv = _make_conv(messages=[_human_msg("")])
    out = conversation_display_title(conv)
    assert out.startswith("Untitled conversation · ")


def test_title_fallback_skips_assistant_messages_when_finding_first_human() -> None:
    ts = datetime(2026, 4, 5, 10, 0, 0, tzinfo=UTC)
    asst = Message(
        uuid="asst",
        sender="assistant",
        parent_uuid=None,
        created_at=ts,
        updated_at=ts,
        content=[TextBlock(text="assistant first")],
    )
    human = _human_msg("human second")
    conv = _make_conv(messages=[asst, human])
    assert conversation_display_title(conv) == "Untitled conversation — human second"


# ---------------------------------------------------------------------------
# block_type — used by the conversation-detail template.
# ---------------------------------------------------------------------------


def test_block_type_classifies_each_concrete_block() -> None:
    assert block_type(TextBlock(text="x")) == "text"
    assert block_type(ThinkingBlock(thinking="y")) == "thinking"
    assert block_type(ToolUseBlock(id="t1", name="web_search", input={})) == "tool_use"
    assert (
        block_type(ToolResultBlock(tool_use_id="t1", name="web_search", is_error=False, inner=[]))
        == "tool_result"
    )


def test_block_type_returns_unknown_for_other_objects() -> None:
    from viewer.parser import UnknownBlock

    assert block_type("not a block") == "unknown"
    # The preserved unknown-block slot (v1.2 Session N) dispatches to the
    # same template branch; the card names the raw type itself.
    assert block_type(UnknownBlock(type="voice_note", raw={})) == "unknown"


# ---------------------------------------------------------------------------
# compute_render_path — branching mechanism (plan §12 step 3).
# All these tests load the synthetic fixture and operate on conv-4, which is
# built specifically to exercise branching: f2 → [f3, f4]; f3 → [f5].
# Canonical leaf-most-by-time path is [f2, f3, f5]; f4 is the regenerate-sibling.
# ---------------------------------------------------------------------------


_F2 = "00000000-0000-0000-0000-0000000000f2"
_F3 = "00000000-0000-0000-0000-0000000000f3"
_F4 = "00000000-0000-0000-0000-0000000000f4"
_F5 = "00000000-0000-0000-0000-0000000000f5"


def _branching_conv(export):
    return next(c for c in export.conversations if "branching" in c.title.lower())


def test_render_path_no_overrides_returns_canonical(export) -> None:
    conv = _branching_conv(export)
    path, ignored = compute_render_path(conv, [])
    assert path == [_F2, _F3, _F5]
    assert ignored == []


def test_render_path_override_to_non_canonical_sibling(export) -> None:
    conv = _branching_conv(export)
    path, ignored = compute_render_path(conv, [_F4])
    # f4 has no children, so the path stops there.
    assert path == [_F2, _F4]
    assert ignored == []


def test_render_path_override_to_canonical_sibling_is_no_op(export) -> None:
    conv = _branching_conv(export)
    path, ignored = compute_render_path(conv, [_F3])
    # f3 IS the canonical child at f2; redundant override but harmless.
    assert path == [_F2, _F3, _F5]
    assert ignored == []  # f3 was a child of f2 → consumed


def test_render_path_unknown_override_is_ignored(export) -> None:
    conv = _branching_conv(export)
    bogus = "00000000-0000-0000-0000-000000000bad"
    path, ignored = compute_render_path(conv, [bogus])
    assert path == [_F2, _F3, _F5]
    assert ignored == [bogus]


def test_render_path_mixed_known_and_unknown_overrides(export) -> None:
    conv = _branching_conv(export)
    bogus = "00000000-0000-0000-0000-000000000bad"
    path, ignored = compute_render_path(conv, [_F4, bogus])
    assert path == [_F2, _F4]
    assert ignored == [bogus]


def test_render_path_empty_canonical_returns_empty() -> None:
    """A conversation whose canonical_path is empty returns no path even with overrides."""
    conv = _make_conv()  # zero messages → empty canonical_path
    path, ignored = compute_render_path(conv, ["x"])
    assert path == []
    assert ignored == ["x"]


# ---------------------------------------------------------------------------
# summary_preview — plan §12 step 4 fix for the multi-paragraph-summary row.
# ---------------------------------------------------------------------------


def test_summary_preview_strips_markdown() -> None:
    out = summary_preview("**Bold** and *italic* and `code` and [link](https://x.invalid)")
    # No raw markers and no HTML tags survive.
    assert "*" not in out
    assert "[" not in out
    assert "<" not in out
    assert "Bold" in out
    assert "italic" in out
    assert "link" in out


def test_summary_preview_collapses_whitespace() -> None:
    out = summary_preview("Line one.\n\n\nLine    two.\nLine three.")
    assert "Line one. Line two. Line three." == out


def test_summary_preview_truncates_with_ellipsis() -> None:
    out = summary_preview("x" * 500, max_chars=200)
    assert out.endswith("…")
    assert len(out) == 201  # 200 chars + ellipsis


def test_summary_preview_does_not_truncate_short_text() -> None:
    out = summary_preview("short summary")
    assert out == "short summary"
    assert "…" not in out


def test_summary_preview_handles_none_and_empty() -> None:
    assert summary_preview(None) == ""
    assert summary_preview("") == ""


def test_summary_preview_strips_headings_and_lists() -> None:
    src = "# Heading\n\n- bullet 1\n- bullet 2\n\n## Subheading\n\nbody"
    out = summary_preview(src)
    assert "#" not in out
    assert "-" not in out
    # Plain text content survives.
    assert "Heading" in out
    assert "bullet 1" in out
    assert "body" in out


# ---------------------------------------------------------------------------
# split_memory_sections — collapsible memory view (plan §12 polish).
# ---------------------------------------------------------------------------


def test_split_memory_all_four_standard_bold_sections() -> None:
    body = (
        "**Work context**\n\nAlex works on X.\n\n"
        "**Personal context**\n\nAlex enjoys hiking.\n\n"
        "**Top of mind**\n\nLooking at vehicles.\n\n"
        "**Brief history**\n\nRecent months building stuff.\n"
    )
    sections = split_memory_sections(body)
    titles = [t for t, _ in sections]
    assert titles == ["Work context", "Personal context", "Top of mind", "Brief history"]
    contents = [c for _, c in sections]
    assert contents[0] == "Alex works on X."
    assert "hiking" in contents[1]


def test_split_memory_only_some_sections() -> None:
    body = "**Work context**\n\nbody one.\n\n**Top of mind**\n\nbody two.\n"
    sections = split_memory_sections(body)
    assert [t for t, _ in sections] == ["Work context", "Top of mind"]


def test_split_memory_non_standard_section_names() -> None:
    body = "**Active projects**\n\nfoo\n\n**Recent threads**\n\nbar\n"
    sections = split_memory_sections(body)
    assert [t for t, _ in sections] == ["Active projects", "Recent threads"]


def test_split_memory_no_sections_falls_back_to_one_block() -> None:
    body = "Just a freeform paragraph with **inline bold** mid-sentence."
    sections = split_memory_sections(body)
    assert len(sections) == 1
    assert sections[0][0] == ""  # title empty → preamble / single-block fallback
    assert sections[0][1] == body


def test_split_memory_uses_atx_headings() -> None:
    body = "## Work context\n\nbody one.\n\n## Personal context\n\nbody two.\n"
    sections = split_memory_sections(body)
    assert [t for t, _ in sections] == ["Work context", "Personal context"]


def test_split_memory_mixed_bold_and_atx() -> None:
    """Real bodies sometimes use both forms; treat them at the same level."""
    body = "**Work context**\n\nbody.\n\n## Personal context\n\nmore.\n"
    sections = split_memory_sections(body)
    assert [t for t, _ in sections] == ["Work context", "Personal context"]


def test_split_memory_preamble_before_first_heading() -> None:
    """Content before the first heading becomes a title-less preamble."""
    body = "Intro line.\n\n**Work context**\n\nthe rest."
    sections = split_memory_sections(body)
    assert sections[0] == ("", "Intro line.")
    assert sections[1] == ("Work context", "the rest.")


def test_split_memory_inline_italic_not_section_header() -> None:
    """``*Recent months*`` (single-asterisk italic) must not be misread as
    a section heading — only ``**Bold**`` paragraph form counts."""
    body = "**Brief history**\n\n*Recent months*\n\nalex has been building.\n"
    sections = split_memory_sections(body)
    assert [t for t, _ in sections] == ["Brief history"]
    # The italic line stays inside the section body.
    assert "Recent months" in sections[0][1]


def test_split_memory_bold_with_extra_text_on_line_not_a_heading() -> None:
    """``**Bold word** with text after`` — the heading regex requires the
    line to end immediately after the closing ``**``."""
    body = "**Bold word** within a sentence.\n\nmore text."
    sections = split_memory_sections(body)
    assert sections[0][0] == ""  # no heading detected → single-block fallback


def test_split_memory_empty_input() -> None:
    assert split_memory_sections("") == [("", "")]
    assert split_memory_sections(None) == [("", "")]
    assert split_memory_sections("   \n  \n") == [("", "   \n  \n")]


def test_split_memory_section_with_blank_content() -> None:
    """A heading with no content under it should still appear, with empty body."""
    body = "**Empty section**\n\n**Next section**\n\nbody.\n"
    sections = split_memory_sections(body)
    titles = [t for t, _ in sections]
    assert "Empty section" in titles
    assert "Next section" in titles


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert slugify("Work context") == "work-context"
    assert slugify("Top of mind") == "top-of-mind"


def test_slugify_strips_punctuation_and_collapses() -> None:
    assert slugify("What's new!") == "what-s-new"
    assert slugify("A — B") == "a-b"


def test_slugify_empty_or_garbage_returns_default() -> None:
    assert slugify("") == "section"
    assert slugify(None) == "section"
    assert slugify("!!!") == "section"
    assert slugify("   ") == "section"
