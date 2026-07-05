"""Error-path tests for ``viewer.parser``.

One test per fixture in plan §5b. For raising fixtures we assert exception type
+ message substring; for warning fixtures we assert log content + that the
parsed result is structurally what the spec promises.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from viewer.parser import (
    ExportFormatError,
    TextBlock,
    UnknownBlock,
    load_export,
)

# ---------------------------------------------------------------------------
# Fixtures that should raise.
# ---------------------------------------------------------------------------


def test_missing_users_raises(fixtures_dir: Path) -> None:
    with pytest.raises(ExportFormatError, match="missing required file: users.json"):
        load_export(fixtures_dir / "bad_missing_users.zip")


def test_missing_conversations_raises(fixtures_dir: Path) -> None:
    with pytest.raises(ExportFormatError, match="missing required file: conversations.json"):
        load_export(fixtures_dir / "bad_missing_conversations.zip")


def test_missing_memories_raises(fixtures_dir: Path) -> None:
    with pytest.raises(ExportFormatError, match="missing required file: memories.json"):
        load_export(fixtures_dir / "bad_missing_memories.zip")


def test_invalid_json_raises(fixtures_dir: Path) -> None:
    with pytest.raises(ExportFormatError, match="invalid JSON in conversations.json"):
        load_export(fixtures_dir / "bad_invalid_json.zip")


def test_users_multiple_raises(fixtures_dir: Path) -> None:
    with pytest.raises(ExportFormatError, match="exactly one record"):
        load_export(fixtures_dir / "bad_users_multiple.zip")


def test_missing_field_raises(fixtures_dir: Path) -> None:
    with pytest.raises(ExportFormatError, match="missing required field: chat_messages"):
        load_export(fixtures_dir / "bad_missing_field.zip")


# ---------------------------------------------------------------------------
# Fixtures that should parse successfully (sometimes with a warning).
# ---------------------------------------------------------------------------


def test_empty_conversations_succeeds(fixtures_dir: Path) -> None:
    export = load_export(fixtures_dir / "bad_empty_conversations.zip")
    assert export.conversations == []


def test_unknown_block_logged_and_preserved(fixtures_dir: Path, caplog) -> None:
    """Preserve-and-flag (v1.2 Session N): an unknown raw block type reaches
    the model as an UnknownBlock carrying the whole raw record, instead of
    being dropped after the warning."""
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_unknown_block.zip")
    assert any(
        "speculation" in rec.getMessage() and "preserved" in rec.getMessage()
        for rec in caplog.records
    )

    conv = next(c for c in export.conversations if "minimal" in c.title.lower())
    assistant = conv.messages[1]
    # The injected speculation block was at index 0; original text blocks follow.
    unknown = assistant.content[0]
    assert isinstance(unknown, UnknownBlock)
    assert unknown.type == "speculation"
    assert unknown.raw["text"] == "Should be skipped with a warning."
    assert all(isinstance(b, TextBlock) for b in assistant.content[1:])
    # The repr must not leak the raw record's content (house rule — same
    # reason Message overrides its repr).
    assert "Should be skipped" not in repr(unknown)


def test_orphan_parent_logged_and_treated_as_root(
    fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_orphan_parent.zip")
    assert any("orphan parent_message_uuid" in rec.getMessage() for rec in caplog.records)

    conv = next(c for c in export.conversations if "minimal" in c.title.lower())
    # The assistant message had its parent rewritten to a uuid not present in
    # the conversation, so it becomes its own root. The leaf-most-by-timestamp
    # leaf is the assistant (later created_at), so the canonical path ends at it.
    assistant = conv.messages[1]
    assert conv.canonical_path[-1] == assistant.uuid


def test_multiple_roots_logged(fixtures_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_multiple_roots.zip")
    assert any("root messages" in rec.getMessage() for rec in caplog.records)

    conv = next(c for c in export.conversations if "minimal" in c.title.lower())
    # Both messages are roots in this fixture; canonical_path picks the leaf
    # with the latest created_at (they share a timestamp here, so just confirm
    # it has length 1 — only one of the two-root chains can win).
    assert len(conv.canonical_path) == 1


def test_sentinel_variants_normalise_to_root(
    fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_sentinel_variants.zip")

    conv = next(c for c in export.conversations if "Sentinel parent variants" in c.title)
    assert all(m.parent_uuid is None for m in conv.messages)
    # No "orphan parent" warning expected — these are valid root forms.
    assert not any("orphan parent_message_uuid" in rec.getMessage() for rec in caplog.records)


def test_cycle_self_excluded_and_logged(
    fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_cycle_self.zip")
    assert any("unreachable" in rec.getMessage() for rec in caplog.records)

    conv = next(c for c in export.conversations if "Self-loop" in c.title)
    # The self-loop message is unreachable; the other message (assistant)
    # has a normal parent pointing at the cycle node — also unreachable.
    self_loop_uuid = conv.messages[0].uuid
    assert self_loop_uuid not in conv.canonical_path


def test_cycle_two_node_excluded_and_logged(
    fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_cycle_two_node.zip")
    assert any("unreachable" in rec.getMessage() for rec in caplog.records)

    conv = next(c for c in export.conversations if "Two-node cycle" in c.title)
    a, b = conv.messages
    assert a.uuid not in conv.canonical_path
    assert b.uuid not in conv.canonical_path


def test_empty_content_succeeds(fixtures_dir: Path) -> None:
    export = load_export(fixtures_dir / "bad_empty_content.zip")
    conv = next(c for c in export.conversations if "minimal" in c.title.lower())
    assistant = conv.messages[1]
    assert assistant.content == []


def test_dangling_memory_collected(fixtures_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_dangling_memory.zip")
    assert any("dangling project memory" in rec.getMessage() for rec in caplog.records)

    assert len(export.orphan_project_memories) == 1
    orphan = export.orphan_project_memories[0]
    assert orphan.project_uuid == "00000000-0000-0000-0000-00000000dead"
    # Existing project still has its memory attached.
    proj_a = next(p for p in export.projects if p.name == "Synthetic Project A")
    assert proj_a.memory is not None


def test_unknown_sender_retained(fixtures_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / "bad_unknown_sender.zip")
    assert any("unknown sender" in rec.getMessage() for rec in caplog.records)

    conv = next(c for c in export.conversations if "minimal" in c.title.lower())
    assert conv.messages[0].sender == "system"


def test_account_mismatch_logged(fixtures_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        load_export(fixtures_dir / "bad_account_mismatch.zip")
    assert any("account uuid mismatch" in rec.getMessage() for rec in caplog.records)


def test_batch_nonzero_filename_logs_warning(
    fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fname = "data-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa-1700000000-deadbeef-batch-0001.zip"
    with caplog.at_level(logging.WARNING, logger="viewer.parser"):
        export = load_export(fixtures_dir / fname)
    assert any("batch index 0001" in rec.getMessage() for rec in caplog.records)
    # Body parses normally.
    assert export.user.full_name == "Synthetic User"
    assert len(export.conversations) == 4
