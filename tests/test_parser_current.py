"""Current-format container support (v1.3.0 Session S Phase 2).

Covers the design record's §2 (content-based detection), §3 (set loading,
any subset, multi-part merge), and §6 (model slots, including the typed
injected-prompt block and the deliberate signature drop) against the
``tests/fixtures/current*`` sets, plus an aggregate-only skip test against
the operator's real current-format export when present.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import pytest

from tests import real_exports
from tests.fixtures.build_fixtures import MANIFEST_FILENAME, SIGNATURE_SENTINEL
from viewer.cache import parser_module_hash, write_cache
from viewer.parser import (
    ALL_CATEGORIES,
    CONTAINER_LEGACY,
    Export,
    ExportFormatError,
    InjectedPromptBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    load_export,
    load_export_from_bytes,
    sniff_container,
    sniff_manifest,
)

CONV_MINIMAL = "00000000-0000-0000-0000-0000000000c1"
DANGLING_PROJECT = "00000000-0000-0000-0000-00000000dead"


@pytest.fixture
def current_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "current"


@pytest.fixture
def current_export(current_dir: Path) -> Export:
    return load_export(current_dir)


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def _thinking_conv_blocks(export: Export):
    conv = next(c for c in export.conversations if "thinking" in c.title.lower())
    return conv.messages[1].content


# ---------------------------------------------------------------------------
# §2 — detection by content, never filename
# ---------------------------------------------------------------------------


def test_sniff_identifies_every_container_kind(fixtures_dir: Path, current_dir: Path) -> None:
    assert sniff_container(_zip_names(fixtures_dir / "synthetic_export.zip")) == CONTAINER_LEGACY
    assert sniff_container(_zip_names(current_dir / "conversations-000.zip")) == "conversations"
    assert sniff_container(_zip_names(current_dir / "projects-000.zip")) == "projects"
    assert sniff_container(_zip_names(current_dir / "memories-000.zip")) == "memories"
    assert sniff_container(_zip_names(current_dir / "light_metadata-000.zip")) == "light_metadata"


def test_sniff_ignores_filenames(current_dir: Path, tmp_path: Path) -> None:
    """A category zip under a misleading name still identifies by content."""
    misleading = tmp_path / "definitely-a-legacy-export.zip"
    misleading.write_bytes((current_dir / "conversations-000.zip").read_bytes())
    export = load_export(misleading)
    assert len(export.conversations) == 4
    assert export.categories == ("conversations",)


def test_sniff_rejects_layouts_matching_neither_format(fixtures_dir: Path) -> None:
    # A damaged legacy archive matches no current signature, so it takes the
    # legacy pipeline and keeps its precise required-files error.
    assert sniff_container(_zip_names(fixtures_dir / "bad_missing_users.zip")) is None
    assert sniff_container({"weird.txt"}) is None
    assert sniff_container(set()) is None


def test_sniff_manifest(current_dir: Path) -> None:
    assert sniff_manifest((current_dir / MANIFEST_FILENAME).read_bytes())
    assert not sniff_manifest(b'{"not_a_manifest": true}')
    assert not sniff_manifest(b"[1, 2, 3]")
    assert not sniff_manifest(b"not json at all")


def test_mixing_containers_is_refused(fixtures_dir: Path, current_dir: Path) -> None:
    with pytest.raises(ExportFormatError, match="cannot mix"):
        load_export([fixtures_dir / "synthetic_export.zip", current_dir / "conversations-000.zip"])


# ---------------------------------------------------------------------------
# §3 — full-set loading
# ---------------------------------------------------------------------------


def test_full_set_loads_from_directory(current_export: Export) -> None:
    assert len(current_export.conversations) == 4
    assert sum(len(c.messages) for c in current_export.conversations) == 10
    assert len(current_export.projects) == 2
    assert current_export.user is not None
    assert current_export.user.full_name == "Synthetic User"
    assert current_export.global_memory is not None
    assert current_export.global_memory.content.startswith("**Work context**")
    assert current_export.orphan_project_memories == []
    assert current_export.categories == ALL_CATEGORIES
    # One source path per category zip; the manifest is metadata, not a source.
    assert len(current_export.source_paths) == 4


def test_memory_files_parsed_from_dict_shape(current_export: Export) -> None:
    files = {f.path: f for f in current_export.memory_files}
    assert set(files) == {
        "/profile.md",
        "/people/synthetic-colleague.md",
        "/topics/nested/deeper-topic.md",
        "/preferences.md",
    }
    assert files["/profile.md"].content.startswith("---\n")
    assert "frontmatter" in files["/preferences.md"].content  # the fence-less file
    stamps = {f.updated_at for f in current_export.memory_files}
    assert len(stamps) == 4  # per-file updated_at survives, all distinct


def test_login_history_parsed(current_export: Export) -> None:
    events = current_export.login_events
    assert len(events) == 3
    assert {e.ip_address for e in events} == {"203.0.113.10", "198.51.100.7", "192.0.2.55"}
    assert all(e.method == "magic_link" for e in events)
    assert all(e.timestamp.tzinfo is not None for e in events)
    firefox = next(e for e in events if e.browser_family == "Firefox")
    assert firefox.os_family == "Windows"
    assert firefox.city is None and firefox.country == "US"


# ---------------------------------------------------------------------------
# §3 — subsets: no required file, features degrade visibly
# ---------------------------------------------------------------------------


def test_conversations_only_subset(current_dir: Path) -> None:
    export = load_export(current_dir / "conversations-000.zip")
    assert len(export.conversations) == 4
    assert export.user is None
    assert export.global_memory is None
    assert export.projects == []
    assert export.login_events == []
    assert export.categories == ("conversations",)


def test_light_metadata_only_subset(current_dir: Path) -> None:
    export = load_export(current_dir / "light_metadata-000.zip")
    assert export.user is not None
    assert len(export.login_events) == 3
    assert export.conversations == []
    assert export.global_memory is None
    assert export.categories == ("light_metadata",)


def test_memories_without_projects_flags_all_project_memories(
    current_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """With no projects category, every project memory dangles — preserved
    and surfaced, never dropped."""
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(current_dir / "memories-000.zip")
    assert any("dangling project memory" in r.getMessage() for r in caplog.records)
    assert len(export.orphan_project_memories) == 1  # proj-A's memory
    assert export.global_memory is not None
    assert len(export.memory_files) == 4
    assert export.categories == ("memories",)


def test_memories_with_projects_resolves_memories(current_dir: Path) -> None:
    export = load_export([current_dir / "projects-000.zip", current_dir / "memories-000.zip"])
    proj_a = next(p for p in export.projects if p.name == "Synthetic Project A")
    assert proj_a.memory is not None
    assert export.orphan_project_memories == []
    assert export.categories == ("projects", "memories")


def test_dangling_variant_set(current_dir: Path, fixtures_dir: Path) -> None:
    """The current-format twin of ``bad_dangling_memory.zip``: the swap-in
    memories zip carries a memory for a project no file describes."""
    export = load_export(
        [
            current_dir / "conversations-000.zip",
            current_dir / "projects-000.zip",
            fixtures_dir / "current_dangling" / "memories-000.zip",
            current_dir / "light_metadata-000.zip",
        ]
    )
    assert len(export.orphan_project_memories) == 1
    assert export.orphan_project_memories[0].project_uuid == DANGLING_PROJECT
    # The resolvable memory still resolves.
    proj_a = next(p for p in export.projects if p.name == "Synthetic Project A")
    assert proj_a.memory is not None


def test_category_zip_loads_from_bytes(current_dir: Path) -> None:
    """Inspect mode's in-memory path gets the same detection dispatch."""
    data = (current_dir / "conversations-000.zip").read_bytes()
    export = load_export_from_bytes(data, name="conversations-000.zip")
    assert len(export.conversations) == 4
    assert export.user is None
    assert export.source_paths == ["conversations-000.zip"]


# ---------------------------------------------------------------------------
# §3 — multi-part merge (provisional; no multi-part export observed)
# ---------------------------------------------------------------------------


def test_two_part_conversations_merge(fixtures_dir: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "current_two_part")
    uuids = [c.uuid for c in export.conversations]
    assert len(uuids) == 4
    assert len(set(uuids)) == 4
    # The duplicate uuid in part 001 was flagged, and the part-000 copy won.
    assert any("duplicate conversation uuid" in r.getMessage() for r in caplog.records)
    dup = next(c for c in export.conversations if c.uuid == CONV_MINIMAL)
    assert dup.title == "Synthetic minimal conversation"  # not the part-001 mutation


def test_isolated_later_part_warns(fixtures_dir: Path, caplog) -> None:
    """The legacy batch warning's current-format sibling: a lone
    ``conversations-001.zip`` suggests part 000 is missing."""
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        load_export(fixtures_dir / "current_two_part" / "conversations-001.zip")
    assert any("missing part number(s): 000" in r.getMessage() for r in caplog.records)


def test_complete_parts_do_not_warn(fixtures_dir: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        load_export(fixtures_dir / "current_two_part")
    assert not any("parts look incomplete" in r.getMessage() for r in caplog.records)


def test_memories_zip_with_two_records_raises(current_dir: Path) -> None:
    """Two ``memories/<uuid>.json`` members in one zip breaks the observed
    single-file invariant — a structural raise, not a silent pick."""
    with zipfile.ZipFile(current_dir / "memories-000.zip") as zf:
        member = next(n for n in zf.namelist() if n.endswith(".json"))
        body = zf.read(member)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(member, body)
        zf.writestr("memories/00000000-0000-0000-0000-00000000beef.json", body)
    with pytest.raises(ExportFormatError, match="exactly one"):
        load_export_from_bytes(out.getvalue(), name="memories-000.zip")


# ---------------------------------------------------------------------------
# §6 — model slots
# ---------------------------------------------------------------------------


def test_injected_prompt_block_is_typed(current_export: Export) -> None:
    conv = next(c for c in current_export.conversations if c.uuid == CONV_MINIMAL)
    block = conv.messages[0].content[0]
    assert isinstance(block, InjectedPromptBlock)
    assert block.prompt.startswith("Synthetic injected instruction")
    assert block.initial_turn_only is True
    assert block.skip_on_truncated_continuation is False
    # The fallback surfaces (placeholder card, serializer) name blocks via
    # ``.type`` — the typed block keeps the raw type string there.
    assert block.type == "injected_prompt_block"


def test_thinking_summaries_and_hidden_flag(current_export: Export) -> None:
    blocks = _thinking_conv_blocks(current_export)
    with_summaries, without_summaries, hidden = blocks[0], blocks[1], blocks[2]
    assert isinstance(with_summaries, ThinkingBlock)
    assert with_summaries.summaries == [
        "Considered what the request needs.",
        "Planned a web search.",
    ]
    assert with_summaries.thinking_hidden is False
    assert without_summaries.summaries == []
    assert hidden.thinking_hidden is True
    assert hidden.summaries == ["Summary of the hidden pass."]


def test_signature_reaches_neither_model_nor_cache(current_export: Export, tmp_path: Path) -> None:
    """The deliberate drop, asserted end to end: the fixture's thinking block
    carries a signature sentinel; no model slot holds it, and the pickled
    parse cache contains no trace of its bytes."""
    for conv in current_export.conversations:
        for msg in conv.messages:
            for block in msg.content:
                assert not hasattr(block, "signature")

    pkl = tmp_path / "parsed.pkl"
    write_cache(pkl, ("set-fingerprint", parser_module_hash()), current_export)
    assert SIGNATURE_SENTINEL.encode("utf-8") not in pkl.read_bytes()


def test_text_block_citations_preserved_with_offsets(current_export: Export) -> None:
    blocks = _thinking_conv_blocks(current_export)
    final_text = blocks[-1]
    assert isinstance(final_text, TextBlock)
    assert len(final_text.citations) == 2
    first = final_text.citations[0]
    assert first["start_index"] == 0
    assert first["end_index"] == 4
    assert first["details"]["url"] == "https://example.invalid/cited-1"
    # Offsets index the raw text (record §9.4's reason for a Sources list).
    assert 0 <= first["start_index"] < first["end_index"] <= len(final_text.text)


def test_display_content_preserved_on_tool_blocks(current_export: Export) -> None:
    blocks = _thinking_conv_blocks(current_export)
    seen: dict[str, str] = {}
    for b in blocks:
        if isinstance(b, (ToolUseBlock, ToolResultBlock)) and b.display_content:
            seen[b.display_content["type"]] = type(b).__name__
    assert set(seen) == {"text", "table", "rich_link", "json_block", "rich_content"}
    # The sixth type rides the artifact conversation.
    artifact_conv = next(c for c in current_export.conversations if "artifact" in c.title)
    artifact_use = artifact_conv.messages[1].content[0]
    assert isinstance(artifact_use, ToolUseBlock)
    assert artifact_use.display_content["type"] == "code_block"
    assert artifact_use.display_content["language"] == "html"


def test_output_format_category_md(current_export: Export) -> None:
    blocks = _thinking_conv_blocks(current_export)
    md_results = [
        b for b in blocks if isinstance(b, ToolResultBlock) and b.output_format_category == "md"
    ]
    assert len(md_results) == 1
    assert md_results[0].inner[0]["text"].startswith("**Bold**")


def test_knowledge_links_ride_inner_blocks(current_export: Export) -> None:
    blocks = _thinking_conv_blocks(current_export)
    search_result = next(b for b in blocks if isinstance(b, ToolResultBlock))
    knowledge = search_result.inner[0]
    assert knowledge["type"] == "knowledge"
    assert knowledge["links"] == [
        {"title": "Related page", "url": "https://example.invalid/related"}
    ]


def test_per_block_timestamps_preserved_raw(current_export: Export) -> None:
    blocks = _thinking_conv_blocks(current_export)
    final_text = blocks[-1]
    assert final_text.start_timestamp == "2026-04-05T10:11:30.196242Z"
    assert final_text.stop_timestamp == "2026-04-05T10:11:30.196242Z"


def test_legacy_parse_carries_the_same_slots_empty(fixtures_dir: Path) -> None:
    """The legacy container flows through the same block model — new slots
    just stay at their defaults."""
    export = load_export(fixtures_dir / "synthetic_export.zip")
    conv = next(c for c in export.conversations if "thinking" in c.title.lower())
    thinking = conv.messages[1].content[0]
    assert isinstance(thinking, ThinkingBlock)
    assert thinking.summaries == []
    assert thinking.thinking_hidden is False
    assert export.memory_files == []
    assert export.login_events == []
    assert export.categories == ALL_CATEGORIES


# ---------------------------------------------------------------------------
# Real-data integration (aggregate-only; skipped without the operator's set)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("real_set", real_exports.params("set"))
def test_real_current_sets_hold_the_invariants(real_set: Path | None, caplog) -> None:
    """Current-format twin of test_parser's real-data check: each set directory under
    ``VIEWER_REAL_EXPORT_DIR`` (skipped when unset) parses and holds the invariants.
    Failures print aggregate counts only; parser warnings are silenced."""
    caplog.set_level(logging.CRITICAL, logger="viewer")
    export = real_exports.load_quietly(real_set)
    real_exports.assert_invariants(export)
