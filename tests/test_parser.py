"""Happy-path tests for ``viewer.parser`` against the synthetic fixture.

Plus an opportunistic integration check against the user's real ``data/data.zip``
when present — skipped in CI.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from viewer.parser import (
    Attachment,
    Conversation,
    Export,
    FileRef,
    GlobalMemory,
    Message,
    Project,
    ProjectMemory,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    load_export,
)


@pytest.fixture
def export(fixtures_dir: Path) -> Export:
    return load_export(fixtures_dir / "synthetic_export.zip")


def test_export_top_level_shape(export: Export) -> None:
    assert isinstance(export, Export)
    assert export.user.uuid == "00000000-0000-0000-0000-000000000001"
    assert export.user.full_name == "Synthetic User"
    assert isinstance(export.global_memory, GlobalMemory)
    assert export.global_memory.content
    assert export.global_memory.account_uuid == export.user.uuid
    assert len(export.projects) == 2
    assert len(export.conversations) == 4
    assert export.orphan_project_memories == []
    assert len(export.source_paths) == 1


def test_project_with_memory(export: Export) -> None:
    proj_a = next(p for p in export.projects if p.name == "Synthetic Project A")
    assert isinstance(proj_a, Project)
    assert proj_a.is_starter is False
    assert proj_a.prompt_template
    assert len(proj_a.docs) == 2
    assert isinstance(proj_a.memory, ProjectMemory)
    assert proj_a.memory.project_uuid == proj_a.uuid
    assert proj_a.memory.account_uuid == export.user.uuid


def test_starter_project_without_memory(export: Export) -> None:
    proj_b = next(p for p in export.projects if p.is_starter)
    assert proj_b.memory is None
    assert proj_b.prompt_template == ""
    assert len(proj_b.docs) == 1


def test_conversation_timestamps_are_utc_aware(export: Export) -> None:
    for conv in export.conversations:
        assert conv.created_at.tzinfo is not None
        assert conv.created_at.utcoffset() == UTC.utcoffset(None)
        for msg in conv.messages:
            assert msg.created_at.tzinfo is not None


def test_conv_1_minimal(export: Export) -> None:
    conv = _conv_titled(export, "minimal")
    assert len(conv.messages) == 2
    human, assistant = conv.messages
    assert human.sender == "human"
    assert assistant.sender == "assistant"
    # Sentinel parent normalised to None.
    assert human.parent_uuid is None
    assert assistant.parent_uuid == human.uuid
    assert all(isinstance(b, TextBlock) for b in human.content)
    assert conv.canonical_path == [human.uuid, assistant.uuid]
    assert conv.branch_points == []
    # children_by_uuid covers every message uuid (key set).
    assert set(conv.children_by_uuid.keys()) == {human.uuid, assistant.uuid}
    assert conv.children_by_uuid[human.uuid] == [assistant.uuid]
    assert conv.children_by_uuid[assistant.uuid] == []


def test_conv_2_thinking_and_tools_covers_every_block_type(export: Export) -> None:
    conv = _conv_titled(export, "thinking")
    assistant = conv.messages[1]
    types = {type(b).__name__ for b in assistant.content}
    assert types == {"ThinkingBlock", "ToolUseBlock", "ToolResultBlock", "TextBlock"}

    thinking = next(b for b in assistant.content if isinstance(b, ThinkingBlock))
    assert thinking.thinking
    assert thinking.truncated is False
    assert thinking.cut_off is False

    tool_use = next(b for b in assistant.content if isinstance(b, ToolUseBlock))
    assert tool_use.name == "web_search"
    assert tool_use.input["query"] == "synthetic query"
    assert tool_use.is_mcp is False

    tool_result = next(b for b in assistant.content if isinstance(b, ToolResultBlock))
    inner_types = {x["type"] for x in tool_result.inner}
    assert inner_types == {"knowledge", "text", "local_resource", "image"}
    assert tool_result.is_error is False


def test_conv_3_artifact_attachment_files(export: Export) -> None:
    conv = _conv_titled(export, "artifact")
    human, assistant = conv.messages

    assert len(human.attachments) == 1
    att = human.attachments[0]
    assert isinstance(att, Attachment)
    assert att.extracted_content == "attached body"
    assert att.file_size == 13

    assert len(human.files) == 1
    fref = human.files[0]
    assert isinstance(fref, FileRef)
    assert fref.file_name == "screenshot.png"

    artifact_use = next(
        b for b in assistant.content if isinstance(b, ToolUseBlock) and b.name == "artifacts"
    )
    assert artifact_use.input["title"] == "Synthetic artifact"
    assert artifact_use.input["language"] == "html"


def test_conv_4_branching_picks_leaf_most_by_time(export: Export) -> None:
    conv = _conv_titled(export, "branching")
    f2 = "00000000-0000-0000-0000-0000000000f2"
    f3 = "00000000-0000-0000-0000-0000000000f3"
    f4 = "00000000-0000-0000-0000-0000000000f4"
    f5 = "00000000-0000-0000-0000-0000000000f5"

    # f5 has the latest created_at, so canonical path runs f2 -> f3 -> f5.
    assert conv.canonical_path == [f2, f3, f5]
    assert conv.branch_points == [f2]
    assert conv.children_by_uuid[f2] == [f3, f4]
    assert conv.children_by_uuid[f3] == [f5]
    # f4 is reachable but not on the canonical path.
    assert f4 not in conv.canonical_path
    assert any(m.uuid == f4 for m in conv.messages)


def test_real_export_loads_with_expected_counts(fixtures_dir: Path) -> None:
    """Integration check against the user's real export, if present."""
    real = fixtures_dir.parent.parent / "data" / "data.zip"
    if not real.exists():
        pytest.skip("real export not present at data/data.zip")

    export = load_export(real)
    assert len(export.conversations) == 134
    assert len(export.projects) == 7

    total_msgs = sum(len(c.messages) for c in export.conversations)
    assert total_msgs == 1817

    project_memories = sum(1 for p in export.projects if p.memory is not None)
    assert project_memories == 3

    branched = sum(1 for c in export.conversations if c.branch_points)
    assert branched == 12

    assert export.orphan_project_memories == []

    # Round-trip sanity: every message uuid appears exactly once in messages.
    seen_msg_uuids: set[str] = set()
    for conv in export.conversations:
        for msg in conv.messages:
            assert msg.uuid not in seen_msg_uuids
            seen_msg_uuids.add(msg.uuid)
        # canonical_path is a contiguous parent-child chain.
        for parent_uuid, child_uuid in zip(
            conv.canonical_path, conv.canonical_path[1:], strict=False
        ):
            child_msg = next(m for m in conv.messages if m.uuid == child_uuid)
            assert child_msg.parent_uuid == parent_uuid


def test_conversation_and_message_repr_omit_body_text(export: Export) -> None:
    """Defence against leaking message bodies into stack traces or log lines."""
    conv = _conv_titled(export, "minimal")
    conv_repr = repr(conv)
    msg_repr = repr(conv.messages[1])

    # Identifying fields are present.
    assert conv.uuid in conv_repr
    assert conv.title in conv_repr
    assert "messages=2" in conv_repr
    assert conv.messages[1].uuid in msg_repr
    assert "sender='assistant'" in msg_repr

    # Message body strings must not appear.
    forbidden = ("hello from human", "hello from assistant")
    for needle in forbidden:
        assert needle not in conv_repr
        assert needle not in msg_repr

    # Repr of a list-of-conversations (e.g. an Export field) inherits this.
    assert "hello from" not in repr(export.conversations)


def _conv_titled(export: Export, needle: str) -> Conversation:
    return next(c for c in export.conversations if needle.lower() in c.title.lower())


# Silence the unused-import lint — Message is part of the public API surface
# this test file is exercising; importing it asserts the symbol exists.
_ = Message
