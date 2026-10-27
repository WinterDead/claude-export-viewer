"""Tests for ``viewer.search`` and the ``/search`` route (plan §12 step 6).

Covers the index lifecycle (drop/recreate, expected row count), querying
behaviours (empty, no-match, malformed), the sentinel collision guard, the
branch-aware link construction in the route, and orphan-memory indexing via
the ``bad_dangling_memory.zip`` fixture.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from app import create_app
from tests.conftest import DUAL_FORMAT_IDS, DUAL_FORMATS
from viewer.parser import load_export
from viewer.search import (
    SENTINEL_CLOSE,
    SENTINEL_OPEN,
    _has_explicit_operators,
    index_export,
    preprocess_query,
    search,
)

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(params=DUAL_FORMATS, ids=DUAL_FORMAT_IDS)
def export_path(request, fixtures_dir: Path) -> Path:
    """Dual-format module (v1.3.0 Session S): export-dependent tests in this
    file run against both container formats."""
    return fixtures_dir / request.param


@pytest.fixture
def dangling_export(fixtures_dir: Path):
    return load_export(fixtures_dir / "bad_dangling_memory.zip")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Index population.
# ---------------------------------------------------------------------------


def test_index_export_populates_expected_row_count(conn, export) -> None:
    """One row per searchable surface — see ``_expected_row_count`` formula."""
    n = index_export(conn, export)
    # 4 conversations + 10 messages + 2 projects + 3 docs + 1 global memory
    # + 1 project memory (proj-A only) + 0 orphans = 21, plus one row per
    # memory file (v1.3.0, record §7 — 4 in the current-format fixture,
    # none in the legacy one), plus one row per artifact and per scheduled
    # task (v1.4.0, export-categories §6.6 — 3 and 2 in the current fixture,
    # none in the legacy one). The invariant moves in lockstep with its kinds.
    assert n == 21 + len(export.memory_files) + len(export.artifacts) + len(export.scheduled_tasks)
    if export.artifacts:
        assert (len(export.artifacts), len(export.scheduled_tasks)) == (3, 2)


def test_index_export_is_idempotent(conn, export) -> None:
    """Calling twice rebuilds — count stays the same, no duplicates."""
    a = index_export(conn, export)
    b = index_export(conn, export)
    assert a == b


def test_index_export_includes_orphan_project_memory(conn, dangling_export) -> None:
    """Orphans MUST be indexed — they're real searchable content even without
    a parent project in the archive."""
    index_export(conn, dangling_export)
    results = search(conn, "orphan")
    assert results.total >= 1
    kinds = {h.kind for h in results.hits}
    assert "memory_project" in kinds
    orphan_hit = next(h for h in results.hits if h.kind == "memory_project")
    assert orphan_hit.project_uuid == "00000000-0000-0000-0000-00000000dead"


# ---------------------------------------------------------------------------
# Query behaviours.
# ---------------------------------------------------------------------------


def test_search_finds_text_in_message_body(conn, export) -> None:
    index_export(conn, export)
    results = search(conn, "hello")
    assert results.total >= 1
    msg_hits = [h for h in results.hits if h.kind == "message"]
    assert msg_hits, "expected at least one message hit for 'hello'"
    # Sentinels must be present in the snippet — they're what the render
    # layer turns into <mark>.
    assert SENTINEL_OPEN in msg_hits[0].snippet
    assert SENTINEL_CLOSE in msg_hits[0].snippet


def test_search_finds_project_meta(conn, export) -> None:
    index_export(conn, export)
    results = search(conn, "synthetic")
    kinds = {h.kind for h in results.hits}
    assert "project_meta" in kinds


def test_search_finds_global_memory(conn, export) -> None:
    index_export(conn, export)
    results = search(conn, '"work context"')
    assert any(h.kind == "memory_global" for h in results.hits)


def test_search_empty_query_returns_no_results_no_error(conn, export) -> None:
    index_export(conn, export)
    results = search(conn, "")
    assert results.hits == []
    assert results.total == 0
    assert results.error is None


def test_search_no_match_returns_zero(conn, export) -> None:
    index_export(conn, export)
    results = search(conn, "zzzzzzzznoneofthese")
    assert results.total == 0
    assert results.hits == []
    assert results.error is None


def test_search_malformed_query_surfaces_error(conn, export) -> None:
    """An unclosed quote is detected as operator syntax (because of the bare
    ``"``) and passed through to FTS5 — which then raises a syntax error.
    Surface a friendly message rather than letting Flask 500."""
    index_export(conn, export)
    results = search(conn, '"unclosed')
    assert results.error is not None
    assert "syntax error" in results.error.lower()
    assert results.hits == []


def test_search_phrase_query(conn, export) -> None:
    """Quoted phrases match exact word sequences."""
    index_export(conn, export)
    results = search(conn, '"hello from human"')
    assert any(
        "hello from human" in h.snippet.replace(SENTINEL_OPEN, "").replace(SENTINEL_CLOSE, "")
        for h in results.hits
    )


def test_search_prefix_query(conn, export) -> None:
    """``hum*`` matches ``human``."""
    index_export(conn, export)
    results = search(conn, "hum*")
    assert results.total >= 1


def test_search_capped_flag(conn, export) -> None:
    """Cap at limit; ``capped`` set when total exceeds limit."""
    index_export(conn, export)
    # A query matching every row in the synthetic fixture — small total, so
    # the cap shouldn't trigger.
    results = search(conn, "synthetic OR hello OR joke OR memory", limit=200)
    assert not results.capped
    assert len(results.hits) <= 200

    # Now force the cap by setting limit below the real total.
    forced = search(conn, "synthetic OR hello OR joke OR memory", limit=2)
    if forced.total > 2:
        assert forced.capped
        assert len(forced.hits) == 2


# ---------------------------------------------------------------------------
# Natural-language query preprocessing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Plain words → each token wrapped in double quotes.
        ("hello", '"hello"'),
        ("hello world", '"hello" "world"'),
        # The original failure mode: trailing punctuation on a token.
        ("Engineering,", '"Engineering,"'),
        ("Electrical Engineering,", '"Electrical" "Engineering,"'),
        # Periods in abbreviations (B.Sc.) — the unicode61 tokenizer strips
        # them inside the phrase, so the wrap is correct.
        ("B.Sc.", '"B.Sc."'),
        ("Electrical Engineering, a B.Sc.", '"Electrical" "Engineering," "a" "B.Sc."'),
        # Apostrophes in contractions.
        ("don't", '"don\'t"'),
        ("he doesn't agree", '"he" "doesn\'t" "agree"'),
        # Hyphens *inside* tokens are natural language, NOT operator syntax.
        ("wifi-enabled", '"wifi-enabled"'),
        ("multi-word-token here", '"multi-word-token" "here"'),
        # Leading hyphen is also natural language — FTS5 doesn't support
        # bare ``-`` exclusion anyway, and product names like TR-808 or
        # covid-19 (or any string that *starts* with ``-``) should match.
        ("foo -bar", '"foo" "-bar"'),
        ("TR-808", '"TR-808"'),
        ("covid-19 numbers", '"covid-19" "numbers"'),
        ("claude-3-5-sonnet", '"claude-3-5-sonnet"'),
        # Email-like fragments.
        ("foo@example.com", '"foo@example.com"'),
        # Version numbers.
        ("v1.2.3", '"v1.2.3"'),
        ("upgrade to v1.2.3 today", '"upgrade" "to" "v1.2.3" "today"'),
        # Multiple internal punctuation marks.
        ("U.S.A.", '"U.S.A."'),
        # Whitespace is collapsed to single-space joiners.
        ("  spaced   words  ", '"spaced" "words"'),
        # Empty / whitespace-only input.
        ("", ""),
        ("   ", ""),
    ],
)
def test_preprocess_natural_language_wraps_tokens(raw, expected) -> None:
    assert preprocess_query(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # Quoted phrase already.
        '"exact phrase"',
        '"hello"',
        # Prefix wildcard.
        "foo*",
        "foo* bar",
        # Boolean operators (case-sensitive uppercase).
        "foo AND bar",
        "foo OR bar",
        "foo NOT bar",
        "(foo OR bar) AND baz",
        # NEAR with and without args.
        "NEAR(foo bar, 5)",
        "foo NEAR bar",
        # Leading ``+`` on a standalone token is the FTS5 phrase-adjacency
        # operator — passes through. (Leading ``-`` deliberately does NOT
        # trigger; see the natural-language test above for ``foo -bar``.)
        "foo +bar",
        "+required only",
    ],
)
def test_preprocess_passes_operator_queries_through(raw) -> None:
    assert preprocess_query(raw) == raw.strip()
    assert _has_explicit_operators(raw.strip())


def test_preprocess_lowercase_and_is_natural_language() -> None:
    """FTS5 only honours uppercase ``AND``/``OR``/``NOT`` as operators, so
    the lowercase form in ``fish and chips`` should be wrapped, not passed."""
    assert preprocess_query("fish and chips") == '"fish" "and" "chips"'


# ---------------------------------------------------------------------------
# Natural-language end-to-end — bare punctuation must NOT 500.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        # The original reproducer.
        "Electrical Engineering, a B.Sc.",
        # Variants — each has bare punctuation that would break a raw
        # MATCH but should now sail through preprocessing.
        "starter project,",
        "non-starter project",
        "B.Sc. is",
        "v1.2.3 release",
        "foo@example.com",
        "don't worry",
        "  trailing comma ,",
        # Leading-hyphen identifiers must run as natural language now that
        # ``-`` no longer triggers operator detection.
        "foo -bar",
        "TR-808 drum",
        "covid-19",
    ],
)
def test_search_handles_natural_language_punctuation(conn, export, raw) -> None:
    """No ``OperationalError``, no ``error`` field — empty results are fine,
    the point is that the query doesn't crash FTS5."""
    index_export(conn, export)
    results = search(conn, raw)
    assert results.error is None, f"unexpected FTS5 error on natural-language query {raw!r}"


def test_search_natural_language_finds_real_matches(conn, export) -> None:
    """End-to-end: 'starter project,' should hit the project_meta rows for
    both project A ('non-starter project ...') and project B ('starter
    project ...')."""
    index_export(conn, export)
    results = search(conn, "starter project,")
    assert results.error is None
    assert any(h.kind == "project_meta" for h in results.hits)


# ---------------------------------------------------------------------------
# Sentinel collision guard.
# ---------------------------------------------------------------------------


def test_sentinel_chars_stripped_with_warning(conn, export, caplog) -> None:
    """Bodies containing pre-existing ``\\x02`` bytes are scrubbed at index
    time so the snippet sentinels remain unambiguous, with a warning logged
    rather than an exception (preserve-and-flag)."""
    # Mutate the parsed export in-memory to add an STX byte to a message body.
    # We do this by replacing one TextBlock's text with a body that contains \x02.
    from dataclasses import replace

    from viewer.parser import TextBlock

    poisoned = "hello \x02 with stx and \x02hit\x02 lookalike"
    conv = export.conversations[0]
    msg = conv.messages[1]  # assistant message
    new_block = TextBlock(text=poisoned)
    new_msg = replace(msg, content=[new_block])
    new_conv = replace(conv, messages=[conv.messages[0], new_msg])
    new_export = replace(
        export,
        conversations=[new_conv, *export.conversations[1:]],
    )

    with caplog.at_level(logging.WARNING, logger="viewer.search"):
        index_export(conn, new_export)
    assert any("STX byte" in rec.message for rec in caplog.records)

    # And the body actually has no STX bytes left in the index.
    row = conn.execute(
        "SELECT body FROM search WHERE kind = 'message' AND message_uuid = ?",
        (new_msg.uuid,),
    ).fetchone()
    assert "\x02" not in row[0]
    assert "stx" in row[0]  # the surrounding text survived


# ---------------------------------------------------------------------------
# Route smoke tests — the only template-level coverage in this module.
# ---------------------------------------------------------------------------


def test_search_route_renders_form_and_hint_on_empty_query(client) -> None:
    body = client.get("/search").get_data(as_text=True)
    assert 'name="q"' in body
    assert "exact phrase" in body or "quotes" in body  # hint text


def test_search_route_renders_results_for_match(client) -> None:
    body = client.get("/search?q=hello").get_data(as_text=True)
    assert "<mark>" in body  # the highlight survived sentinel substitution
    # A conversation link should appear.
    assert "/conversations/" in body


def test_search_route_no_match_renders_empty_state(client) -> None:
    body = client.get("/search?q=zzzznevermatches").get_data(as_text=True)
    assert "No matches" in body


def test_search_route_branch_aware_links(client) -> None:
    """A hit on a non-canonical message links via ``?branch=<uuid>``;
    a hit on a canonical message links without it."""
    # conv-4 fixture: f4 is the non-canonical sibling, body "Joke B".
    body = client.get("/search?q=regenerate").get_data(as_text=True)
    f4 = "00000000-0000-0000-0000-0000000000f4"
    assert f"branch={f4}" in body
    assert f"#msg-{f4}" in body

    # f5 is canonical. Search for its unique text.
    body = client.get("/search?q=another").get_data(as_text=True)
    f5 = "00000000-0000-0000-0000-0000000000f5"
    assert f"#msg-{f5}" in body
    # Canonical hit must NOT carry a branch override.
    assert f"branch={f5}" not in body


def test_search_route_malformed_query_renders_friendly_error(client) -> None:
    body = client.get("/search?q=%22unclosed").get_data(as_text=True)
    assert "syntax error" in body.lower()


# ---------------------------------------------------------------------------
# Memory-file hits (v1.3.0, record §7)
# ---------------------------------------------------------------------------


@pytest.fixture
def current_client(tmp_path: Path, fixtures_dir: Path):
    app = create_app(fixtures_dir / "current", db_path=tmp_path / "viewer-current.sqlite")
    return app.test_client()


def test_memory_file_hits_render_with_path_and_anchor_link(current_client) -> None:
    body = current_client.get("/search?q=colleague").get_data(as_text=True)
    assert "/people/synthetic-colleague.md" in body
    assert "/memory#memory-file-people-synthetic-colleague-md" in body
    assert "<mark>" in body


def test_memory_file_index_rows_are_searchable_by_path(conn, export) -> None:
    if not export.memory_files:
        pytest.skip("legacy run — no memory files to index")
    index_export(conn, export)
    results = search(conn, "preferences")
    assert any(h.kind == "memory_file" for h in results.hits)
    hit = next(h for h in results.hits if h.kind == "memory_file")
    assert hit.title == "/preferences.md"
