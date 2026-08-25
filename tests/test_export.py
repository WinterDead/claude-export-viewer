"""Conversation export (v1.2 Session M): serializer + download endpoints.

Covers the brief's fourteen test areas at the HTTP level, using the synthetic
fixture plus in-memory "exotic" conversations for situations the fixture
doesn't carry (unknown senders, unknown block objects, truncated/cut-off
thinking flags, unpaired tool calls, error results, untitled conversations,
filename collisions). A handful of serializer-level unit tests pin behaviour
the endpoints can't reach directly (YAML escaping, the dormant standalone
tool-result card, the unknown-block fallback).
"""

from __future__ import annotations

import dataclasses
import io
import json
import tomllib
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app import create_app
from tests.conftest import scrape_csrf
from viewer.db import get_db
from viewer.export import (
    ExportOptions,
    bulk_zip_filename,
    export_filename,
    serialise_conversation,
)
from viewer.parser import (
    Attachment,
    Conversation,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from viewer.tagging import NO_PROJECT_LABEL, NO_PROJECT_SENTINEL, set_tag

# ---------------------------------------------------------------------------
# Synthetic-fixture uuids (tests/fixtures/build_fixtures.py).
# ---------------------------------------------------------------------------

CONV_MINIMAL = "00000000-0000-0000-0000-0000000000c1"
CONV_TOOLS = "00000000-0000-0000-0000-0000000000d1"
CONV_ARTIFACT = "00000000-0000-0000-0000-0000000000e1"
CONV_BRANCHING = "00000000-0000-0000-0000-0000000000f1"
BRANCH_B = "00000000-0000-0000-0000-0000000000f4"
PROJECT_A = "00000000-0000-0000-0000-0000000000a1"

# ---------------------------------------------------------------------------
# Exotic conversations, constructed directly (the census found zero flagged
# thinking blocks in real data, and the committed fixture doesn't carry the
# other oddities — in-memory territory per the Session M brief). Since
# Session N the parser also preserves unknown raw block types, so the
# parsed-archive path is exercised separately below.
# ---------------------------------------------------------------------------

TS = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

CONV_FIDELITY = "00000000-0000-0000-0000-0000000000e9"
CONV_TOOL_EDGE = "00000000-0000-0000-0000-0000000000ea"
CONV_FLAGS = "00000000-0000-0000-0000-0000000000eb"
CONV_UNTITLED = "00000000-0000-0000-0000-0000000000ec"
CONV_ODD = "00000000-0000-0000-0000-0000000000ed"
CONV_TWIN_A = "00000000-0000-0000-0000-0000000000ta"
CONV_TWIN_B = "00000000-0000-0000-0000-0000000000tb"
CONV_FULL = "00000000-0000-0000-0000-0000000000ff"

FIDELITY_BODY = (
    "Intro paragraph.\n"
    "\n"
    "## A heading the message itself contains\n"
    "\n"
    "```python\n"
    'print("fenced")\n'
    "```\n"
    "\n"
    "Closing line."
)


class _FauxBlock:
    """A content-block shape the serializer does not recognise."""

    type = "voice_note"


def _mk_msg(
    uuid: str,
    sender: str,
    parent: str | None,
    content: list,
    *,
    attachments: list | None = None,
    files: list | None = None,
    created: datetime = TS,
) -> Message:
    return Message(
        uuid=uuid,
        sender=sender,
        parent_uuid=parent,
        created_at=created,
        updated_at=created,
        content=content,
        attachments=attachments or [],
        files=files or [],
    )


def _linear_conv(uuid: str, title: str, messages: list[Message]) -> Conversation:
    """A conversation whose messages chain linearly in list order."""
    children: dict[str, list[str]] = {m.uuid: [] for m in messages}
    for parent, child in zip(messages, messages[1:], strict=False):
        children[parent.uuid].append(child.uuid)
    return Conversation(
        uuid=uuid,
        title=title,
        summary="",
        created_at=TS,
        updated_at=TS,
        messages=messages,
        children_by_uuid=children,
        canonical_path=[m.uuid for m in messages],
        branch_points=[],
    )


def _conv_fidelity() -> Conversation:
    return _linear_conv(
        CONV_FIDELITY,
        "Fidelity check",
        [
            _mk_msg("ex-fid-1", "human", None, [TextBlock(FIDELITY_BODY)]),
            _mk_msg("ex-fid-2", "assistant", "ex-fid-1", [TextBlock("Understood.")]),
        ],
    )


def _conv_tool_edge() -> Conversation:
    return _linear_conv(
        CONV_TOOL_EDGE,
        "Tool edge cases",
        [
            _mk_msg("ex-te-1", "human", None, [TextBlock("Run things.")]),
            _mk_msg(
                "ex-te-2",
                "assistant",
                "ex-te-1",
                [
                    # Interrupted call: no result anywhere (two exist in the
                    # real archive — census, Session L).
                    ToolUseBlock(id="", name="web_search", input={}),
                    # Error result: singular inner-block count exercised too.
                    ToolUseBlock(id="", name="bash_tool", input={}),
                    ToolResultBlock(
                        tool_use_id="",
                        name="bash_tool",
                        is_error=True,
                        inner=[{"type": "text", "text": "exit 1"}],
                    ),
                    TextBlock("That failed."),
                ],
            ),
        ],
    )


def _conv_flags() -> Conversation:
    return _linear_conv(
        CONV_FLAGS,
        "Thinking flag variants",
        [
            _mk_msg("ex-fl-1", "human", None, [TextBlock("Think hard.")]),
            _mk_msg(
                "ex-fl-2",
                "assistant",
                "ex-fl-1",
                [
                    ThinkingBlock(thinking="plain reasoning"),
                    ThinkingBlock(thinking="cut short", truncated=True),
                    ThinkingBlock(thinking="lost tail", cut_off=True),
                    ThinkingBlock(thinking="both flags", truncated=True, cut_off=True),
                    TextBlock("Answer."),
                ],
            ),
        ],
    )


def _conv_untitled() -> Conversation:
    return _linear_conv(
        CONV_UNTITLED,
        "",
        [
            _mk_msg(
                "ex-un-1",
                "human",
                None,
                [TextBlock("What is the airspeed velocity of an unladen swallow")],
            ),
            _mk_msg("ex-un-2", "assistant", "ex-un-1", [TextBlock("African or European?")]),
        ],
    )


def _conv_odd() -> Conversation:
    return _linear_conv(
        CONV_ODD,
        "Unknown sender and block",
        [
            _mk_msg("ex-od-1", "system", None, [TextBlock("System interjection.")]),
            _mk_msg(
                "ex-od-2",
                "assistant",
                "ex-od-1",
                [TextBlock("Before the oddity."), _FauxBlock(), TextBlock("After the oddity.")],
            ),
        ],
    )


def _conv_twin(uuid: str, marker: str) -> Conversation:
    return _linear_conv(
        uuid,
        "Twin title",
        [_mk_msg(f"ex-tw-{marker}", "human", None, [TextBlock(f"Twin body {marker}.")])],
    )


def _conv_full() -> Conversation:
    """Fully featured: tagged (by the test), branching, thinking, attachment."""
    t0, t1, t2, t3 = (
        datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 12, 1, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 12, 2, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 12, 3, 0, tzinfo=UTC),
    )
    root = _mk_msg(
        "ex-fu-1",
        "human",
        None,
        [TextBlock("Question?")],
        attachments=[
            Attachment(
                file_name="brief.txt", file_type="txt", file_size=9, extracted_content="the brief"
            )
        ],
        created=t0,
    )
    alt_a = _mk_msg(
        "ex-fu-2",
        "assistant",
        "ex-fu-1",
        [ThinkingBlock(thinking="Weighing options."), TextBlock("Answer A.")],
        created=t1,
    )
    alt_b = _mk_msg("ex-fu-3", "assistant", "ex-fu-1", [TextBlock("Answer B.")], created=t2)
    follow = _mk_msg("ex-fu-4", "human", "ex-fu-2", [TextBlock("Thanks.")], created=t3)
    return Conversation(
        uuid=CONV_FULL,
        title="Fully featured",
        summary="",
        created_at=t0,
        updated_at=t3,
        messages=[root, alt_a, alt_b, follow],
        children_by_uuid={
            "ex-fu-1": ["ex-fu-2", "ex-fu-3"],
            "ex-fu-2": ["ex-fu-4"],
            "ex-fu-3": [],
            "ex-fu-4": [],
        },
        canonical_path=["ex-fu-1", "ex-fu-2", "ex-fu-4"],
        branch_points=["ex-fu-1"],
    )


def _exotic_conversations() -> list[Conversation]:
    return [
        _conv_fidelity(),
        _conv_tool_edge(),
        _conv_flags(),
        _conv_untitled(),
        _conv_odd(),
        _conv_twin(CONV_TWIN_A, "a"),
        _conv_twin(CONV_TWIN_B, "b"),
        _conv_full(),
    ]


# ---------------------------------------------------------------------------
# App fixtures and helpers.
# ---------------------------------------------------------------------------


@pytest.fixture
def client(export, tmp_path: Path):
    extended = dataclasses.replace(
        export, conversations=[*export.conversations, *_exotic_conversations()]
    )
    app = create_app(extended, db_path=tmp_path / "viewer.sqlite")
    return app.test_client()


def _get_export(client, uuid: str, query: str = ""):
    return client.get(f"/conversations/{uuid}/export{query}")


def _get_doc(client, uuid: str, query: str = "") -> str:
    r = _get_export(client, uuid, query)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _frontmatter(doc: str) -> dict[str, str]:
    lines = doc.split("\n")
    assert lines[0] == "---"
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, _, value = line.partition(": ")
        fields[key] = value
    return fields


def _tag(client, conv_uuid: str, project_uuid: str) -> None:
    r = client.post(
        "/tag",
        data={
            "csrf_token": scrape_csrf(client, "/inspect"),
            "conversation_uuid": conv_uuid,
            "project_uuid": project_uuid,
        },
    )
    assert r.status_code == 302


def _post_bulk(client, uuids: list[str], **form):
    data = {"csrf_token": scrape_csrf(client, "/inspect"), "conversation_uuid": uuids, **form}
    return client.post("/conversations/export", data=data)


def _zip_members(response) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


# ---------------------------------------------------------------------------
# 1. Frontmatter
# ---------------------------------------------------------------------------


def test_frontmatter_full_fields_on_fully_featured_conversation(client) -> None:
    _tag(client, CONV_FULL, PROJECT_A)
    doc = _get_doc(client, CONV_FULL)
    fields = _frontmatter(doc)

    assert fields["title"] == '"Fully featured"'
    assert fields["uuid"] == CONV_FULL
    assert fields["created"] == "2026-03-01T12:00:00+00:00"
    assert fields["updated"] == "2026-03-01T12:03:00+00:00"
    assert fields["messages"] == "3"  # count on the exported path, not all four
    assert fields["project"] == '"Synthetic Project A"'
    assert fields["branch_points"] == "1"
    assert fields["thinking"] == "omitted"
    assert fields["attachments"] == "included"
    assert fields["exported"] == date.today().isoformat()
    assert fields["generator"] == f"Claude Export Viewer {_pyproject_version()}"
    # Field order matches the design record's layout.
    assert list(fields) == [
        "title",
        "uuid",
        "created",
        "updated",
        "messages",
        "project",
        "branch_points",
        "thinking",
        "attachments",
        "exported",
        "generator",
    ]


def test_frontmatter_conditional_fields_absent(client) -> None:
    # conv-1: untagged, linear, no thinking, no attachments.
    fields = _frontmatter(_get_doc(client, CONV_MINIMAL))
    assert "project" not in fields
    assert "branch_points" not in fields
    assert "thinking" not in fields
    assert "attachments" not in fields


def test_frontmatter_h1_title_follows(client) -> None:
    doc = _get_doc(client, CONV_MINIMAL)
    assert "\n\n# Synthetic minimal conversation\n" in doc


def test_sentinel_tag_exports_no_project_label(client) -> None:
    """C1 (Session N, operator decision): a conversation tagged No Project
    exports with ``project: "No Project"`` — the triage record is authored
    work and belongs in a full export."""
    _tag(client, CONV_MINIMAL, NO_PROJECT_SENTINEL)
    fields = _frontmatter(_get_doc(client, CONV_MINIMAL))
    assert fields["project"] == f'"{NO_PROJECT_LABEL}"'


def test_orphan_tag_stays_field_absent(client) -> None:
    """C1's carve-out: an orphan tag (project uuid no longer in the archive)
    has no label to resolve, so the field stays absent — as shipped."""
    with client.application.app_context():
        set_tag(get_db(), CONV_MINIMAL, "00000000-0000-0000-0000-00000000dead")
    fields = _frontmatter(_get_doc(client, CONV_MINIMAL))
    assert "project" not in fields


# ---------------------------------------------------------------------------
# 2. Sender headings
# ---------------------------------------------------------------------------


def test_sender_headings_use_display_labels(client) -> None:
    doc = _get_doc(client, CONV_MINIMAL)
    assert "\n## User\n" in doc
    assert "\n## Assistant\n" in doc
    assert "## human" not in doc


def test_unknown_sender_passes_through_raw(client) -> None:
    doc = _get_doc(client, CONV_ODD)
    assert "\n## system\n" in doc


# ---------------------------------------------------------------------------
# 3. Body fidelity
# ---------------------------------------------------------------------------


def test_body_with_headings_and_fences_exports_byte_identical(client) -> None:
    doc = _get_doc(client, CONV_FIDELITY)
    assert FIDELITY_BODY in doc


# ---------------------------------------------------------------------------
# 4. Thinking
# ---------------------------------------------------------------------------


def test_thinking_excluded_by_default_with_omitted_marker(client) -> None:
    doc = _get_doc(client, CONV_TOOLS)
    assert _frontmatter(doc)["thinking"] == "omitted"
    assert "[Thinking]" not in doc
    assert "Reasoning about the request." not in doc


def test_thinking_included_with_flag(client) -> None:
    doc = _get_doc(client, CONV_TOOLS, "?thinking=1")
    assert _frontmatter(doc)["thinking"] == "included"
    assert "> **[Thinking]**\n> Reasoning about the request." in doc


def test_thinking_truncated_and_cut_off_label_variants(client) -> None:
    doc = _get_doc(client, CONV_FLAGS, "?thinking=1")
    assert "> **[Thinking]**\n> plain reasoning" in doc
    assert "> **[Thinking]** · truncated\n> cut short" in doc
    assert "> **[Thinking]** · cut off\n> lost tail" in doc
    assert "> **[Thinking]** · truncated · cut off\n> both flags" in doc


# ---------------------------------------------------------------------------
# 5. Tool cards
# ---------------------------------------------------------------------------


def test_tool_card_with_result(client) -> None:
    doc = _get_doc(client, CONV_TOOLS)
    assert "> **[Tool: web_search]** 4 inner blocks" in doc


def test_tool_card_with_no_result_uses_authored_form(client) -> None:
    doc = _get_doc(client, CONV_TOOL_EDGE)
    assert "> **[Tool: web_search]** — no result recorded." in doc


def test_tool_card_error_result_carries_reading_view_marker(client) -> None:
    # Task 1.2 verification outcome: the reading view appends " · error" to
    # an error result's card meta (templates/_message.html), so the export
    # card carries the same marker.
    doc = _get_doc(client, CONV_TOOL_EDGE)
    assert "> **[Tool: bash_tool]** 1 inner block · error" in doc


def test_standalone_tool_result_gets_its_own_card() -> None:
    # Dormant path — no orphan results exist in the real archive, but the
    # design record's card rule names both block types.
    conv = _linear_conv(
        "ex-standalone",
        "Orphan result",
        [
            _mk_msg(
                "ex-sr-1",
                "assistant",
                None,
                [ToolResultBlock(tool_use_id="", name="web_fetch", is_error=False, inner=[{}, {}])],
            )
        ],
    )
    doc = serialise_conversation(conv, conv.canonical_path)
    assert "> **[Tool: web_fetch]** 2 inner blocks" in doc


# ---------------------------------------------------------------------------
# 6. Attachments
# ---------------------------------------------------------------------------


def test_attachment_full_text_by_default(client) -> None:
    doc = _get_doc(client, CONV_ARTIFACT)
    assert _frontmatter(doc)["attachments"] == "included"
    assert "> **[Attachment: notes.txt]**\n> attached body" in doc


def test_attachment_reference_only_when_toggle_off(client) -> None:
    doc = _get_doc(client, CONV_ARTIFACT, "?attachments=0")
    assert _frontmatter(doc)["attachments"] == "referenced"
    assert "> **[Attachment: notes.txt]** — text not exported." in doc
    assert "attached body" not in doc


# ---------------------------------------------------------------------------
# 7. Files note, artifact placeholder, unknown-block placeholder
# ---------------------------------------------------------------------------


def test_files_note(client) -> None:
    doc = _get_doc(client, CONV_ARTIFACT)
    assert "> **[Files]** 1 binary file attached (not included in export): screenshot.png" in doc


def test_artifact_placeholder_without_content(client) -> None:
    doc = _get_doc(client, CONV_ARTIFACT)
    assert "> **[Artifact: Synthetic artifact (text/html)]** — content not exported." in doc
    assert "<!doctype html>" not in doc  # the artifact body must never travel


def test_unknown_block_placeholder(client) -> None:
    doc = _get_doc(client, CONV_ODD)
    assert "> **[Unrecognised content block: voice_note]**" in doc
    assert "Before the oddity." in doc
    assert "After the oddity." in doc


def test_unknown_block_placeholder_falls_back_to_class_name() -> None:
    conv = _linear_conv(
        "ex-unknown-2",
        "Unknown without type attribute",
        [_mk_msg("ex-ub-1", "assistant", None, [object()])],
    )
    doc = serialise_conversation(conv, conv.canonical_path)
    assert "> **[Unrecognised content block: object]**" in doc


def test_unknown_block_from_parsed_archive_reaches_both_placeholders(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    """Task D reachability (v1.2 Session N): the parser preserves unknown raw
    block types, so a real archive's unknown block reaches the reading view's
    placeholder card AND the export's placeholder line — neither path is
    dormant any more."""
    app = create_app(fixtures_dir / "bad_unknown_block.zip", db_path=tmp_path / "viewer.sqlite")
    client = app.test_client()

    page = client.get(f"/conversations/{CONV_MINIMAL}").get_data(as_text=True)
    assert "block-unknown" in page
    assert "speculation" in page  # the raw type is named on the card

    doc = _get_doc(client, CONV_MINIMAL)
    assert "> **[Unrecognised content block: speculation]**" in doc
    # The raw record's content stays out of the export (placeholder only).
    assert "Should be skipped with a warning." not in doc


# ---------------------------------------------------------------------------
# 8. Branches
# ---------------------------------------------------------------------------

# Byte-exact per the Session N brief (C2) — singular form; the fixture's
# branch point has exactly one alternate.
BRANCH_ANNOTATION = (
    "> **[Branch point]** 1 alternate response exists and is not included in this file."
)


def test_branching_default_path_with_annotation(client) -> None:
    doc = _get_doc(client, CONV_BRANCHING)
    assert "Joke A." in doc
    assert "Joke B (regenerate)." not in doc
    assert BRANCH_ANNOTATION in doc
    fields = _frontmatter(doc)
    assert fields["branch_points"] == "1"
    assert fields["messages"] == "3"


def test_branching_requested_combination_is_exported(client) -> None:
    doc = _get_doc(client, CONV_BRANCHING, f"?branch={BRANCH_B}")
    assert "Joke B (regenerate)." in doc
    assert "Joke A." not in doc
    assert BRANCH_ANNOTATION in doc
    assert _frontmatter(doc)["messages"] == "2"


def test_unknown_branch_override_is_ignored_like_detail_page(client) -> None:
    doc = _get_doc(client, CONV_BRANCHING, "?branch=bogus")
    assert "Joke A." in doc  # falls back to the default path


def test_bulk_uses_default_branch_path(client) -> None:
    members = _zip_members(_post_bulk(client, [CONV_BRANCHING]))
    (doc,) = members.values()
    assert "Joke A." in doc
    assert "Joke B (regenerate)." not in doc
    assert BRANCH_ANNOTATION in doc


def test_non_branching_conversation_has_no_annotation_or_field(client) -> None:
    doc = _get_doc(client, CONV_MINIMAL)
    assert "[Branch point]" not in doc
    assert "branch_points" not in _frontmatter(doc)


def test_branch_annotation_plural_form() -> None:
    """C2 (Session N): the plural annotation, byte-exact — a branch point
    with three children exports two alternates."""
    t0, t1, t2, t3 = (
        datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 12, 1, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 12, 2, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 12, 3, 0, tzinfo=UTC),
    )
    root = _mk_msg("ex-pl-1", "human", None, [TextBlock("Q?")], created=t0)
    kids = [
        _mk_msg(f"ex-pl-{i}", "assistant", "ex-pl-1", [TextBlock(f"A{i}.")], created=t)
        for i, t in (("2", t1), ("3", t2), ("4", t3))
    ]
    conv = Conversation(
        uuid="ex-plural",
        title="Three-way branch",
        summary="",
        created_at=t0,
        updated_at=t3,
        messages=[root, *kids],
        children_by_uuid={"ex-pl-1": ["ex-pl-2", "ex-pl-3", "ex-pl-4"]},
        canonical_path=["ex-pl-1", "ex-pl-4"],
        branch_points=["ex-pl-1"],
    )
    doc = serialise_conversation(conv, conv.canonical_path)
    assert (
        "> **[Branch point]** 2 alternate responses exist and are not included in this file." in doc
    )


# ---------------------------------------------------------------------------
# 9. Naming
# ---------------------------------------------------------------------------


def test_single_export_filename_is_dated_slug(client) -> None:
    r = _get_export(client, CONV_MINIMAL)
    assert r.headers["Content-Disposition"] == (
        'attachment; filename="2026-04-05_synthetic-minimal-conversation.md"'
    )


def test_untitled_conversation_slug_uses_synthesised_preview(client) -> None:
    r = _get_export(client, CONV_UNTITLED)
    assert r.headers["Content-Disposition"] == (
        'attachment; filename="2026-03-01_untitled-conversation'
        '-what-is-the-airspeed-velocity-of-an-unladen-swallow.md"'
    )
    doc = r.get_data(as_text=True)
    assert "# Untitled conversation — What is the airspeed velocity of an unladen swallow" in doc


def test_bulk_collision_gets_uuid_fragment(client) -> None:
    members = _zip_members(_post_bulk(client, [CONV_TWIN_A, CONV_TWIN_B]))
    assert len(members) == 2
    assert "2026-03-01_twin-title.md" in members
    assert f"2026-03-01_twin-title_{CONV_TWIN_B[-8:]}.md" in members


def test_bulk_zip_name_matches_spec(client) -> None:
    r = _post_bulk(client, [CONV_MINIMAL])
    expected = f"claude-conversations_{date.today().isoformat()}.zip"
    assert r.headers["Content-Disposition"] == f'attachment; filename="{expected}"'


def test_filename_helpers() -> None:
    conv = _conv_untitled()
    assert export_filename(conv, fragment="000000ec").endswith("_000000ec.md")
    assert bulk_zip_filename(date(2026, 7, 4)) == "claude-conversations_2026-07-04.zip"


# ---------------------------------------------------------------------------
# 10. Bulk integrity
# ---------------------------------------------------------------------------


def test_bulk_zip_matches_selection_exactly(client) -> None:
    selection = [CONV_MINIMAL, CONV_TOOLS, CONV_ARTIFACT]
    members = _zip_members(_post_bulk(client, selection))
    assert len(members) == 3
    exported_uuids = {_frontmatter(doc)["uuid"] for doc in members.values()}
    assert exported_uuids == set(selection)


def test_bulk_rejects_unknown_conversation(client) -> None:
    r = _post_bulk(client, [CONV_MINIMAL, "no-such-uuid"])
    assert r.status_code == 400


def test_bulk_rejects_empty_selection(client) -> None:
    r = _post_bulk(client, [])
    assert r.status_code == 400


def test_bulk_requires_csrf_token(client) -> None:
    r = client.post("/conversations/export", data={"conversation_uuid": CONV_MINIMAL})
    assert r.status_code == 400


def test_single_export_unknown_conversation_404s(client) -> None:
    assert _get_export(client, "no-such-uuid").status_code == 404


# ---------------------------------------------------------------------------
# 11. Options defaults pinned at both endpoints
# ---------------------------------------------------------------------------


def test_single_endpoint_defaults_thinking_off_attachments_on(client) -> None:
    doc = _get_doc(client, CONV_TOOLS)
    assert _frontmatter(doc)["thinking"] == "omitted"
    doc = _get_doc(client, CONV_ARTIFACT)
    assert _frontmatter(doc)["attachments"] == "included"
    assert "> attached body" in doc


def test_bulk_endpoint_defaults_thinking_off_attachments_on(client) -> None:
    members = _zip_members(_post_bulk(client, [CONV_TOOLS, CONV_ARTIFACT]))
    by_uuid = {_frontmatter(doc)["uuid"]: doc for doc in members.values()}
    assert _frontmatter(by_uuid[CONV_TOOLS])["thinking"] == "omitted"
    assert "[Thinking]" not in by_uuid[CONV_TOOLS]
    assert _frontmatter(by_uuid[CONV_ARTIFACT])["attachments"] == "included"
    assert "> attached body" in by_uuid[CONV_ARTIFACT]


def test_bulk_endpoint_honours_both_toggles(client) -> None:
    members = _zip_members(
        _post_bulk(client, [CONV_TOOLS, CONV_ARTIFACT], thinking="1", attachments="0")
    )
    by_uuid = {_frontmatter(doc)["uuid"]: doc for doc in members.values()}
    assert "> **[Thinking]**\n> Reasoning about the request." in by_uuid[CONV_TOOLS]
    assert _frontmatter(by_uuid[CONV_ARTIFACT])["attachments"] == "referenced"
    assert "> **[Attachment: notes.txt]** — text not exported." in by_uuid[CONV_ARTIFACT]


# ---------------------------------------------------------------------------
# 12. No server-side writes
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_exports_leave_no_disk_trace(app_factory, tmp_path: Path) -> None:
    client = app_factory().test_client()
    cache_dir = tmp_path / "cache"

    client.get("/")  # settle any first-request work before the snapshot
    before = _snapshot(cache_dir)
    assert "viewer.sqlite" in before

    assert _get_export(client, CONV_MINIMAL).status_code == 200
    assert _get_export(client, CONV_TOOLS, "?thinking=1").status_code == 200
    assert _post_bulk(client, [CONV_MINIMAL, CONV_BRANCHING]).status_code == 200

    assert _snapshot(cache_dir) == before


# ---------------------------------------------------------------------------
# 13. Inspect mode
# ---------------------------------------------------------------------------

INSPECTED_TITLE = "Inspected-only conversation"


@pytest.fixture
def inspect_zip_bytes(export_path: Path) -> bytes:
    """The synthetic fixture rebuilt in memory with conv-1 retitled, so a
    test can tell the inspected view from the primary. Never written to
    disk."""
    src = export_path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(src)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    convs = json.loads(files["conversations.json"])
    convs[0]["name"] = INSPECTED_TITLE
    files["conversations.json"] = json.dumps(convs).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, blob in files.items():
            zf.writestr(name, blob)
    return out.getvalue()


def _start_inspection(client, zip_bytes: bytes) -> None:
    r = client.post(
        "/inspect",
        data={
            "csrf_token": scrape_csrf(client, "/inspect"),
            "export_zip": (io.BytesIO(zip_bytes), "other_export.zip"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 302


def test_exports_serve_inspected_content_and_leave_no_trace(
    app_factory, inspect_zip_bytes, tmp_path: Path
) -> None:
    client = app_factory().test_client()
    cache_dir = tmp_path / "cache"

    # Tag conv-1 on the PRIMARY export first: the inspected export shares
    # the uuid, and its exported file must still carry no project field —
    # tags never pair with an inspected export's conversations (Session K).
    _tag(client, CONV_MINIMAL, PROJECT_A)
    fields = _frontmatter(_get_doc(client, CONV_MINIMAL))
    assert fields["project"] == '"Synthetic Project A"'  # primary view sees the tag

    before = _snapshot(cache_dir)
    _start_inspection(client, inspect_zip_bytes)

    # Single export serves the INSPECTED conversation, without the tag.
    doc = _get_doc(client, CONV_MINIMAL)
    assert f"# {INSPECTED_TITLE}" in doc
    assert _frontmatter(doc)["title"] == f'"{INSPECTED_TITLE}"'
    assert "project" not in _frontmatter(doc)

    # Bulk export works against the inspected export too.
    members = _zip_members(_post_bulk(client, [CONV_MINIMAL, CONV_TOOLS]))
    assert len(members) == 2
    assert any(f"# {INSPECTED_TITLE}" in doc for doc in members.values())

    # Exit, and confirm the whole cycle — inspect, export, bulk export,
    # return — left the cache untouched (the K no-trace rule survives).
    r = client.post("/inspect/exit", data={"csrf_token": scrape_csrf(client, "/inspect")})
    assert r.status_code == 302
    assert _snapshot(cache_dir) == before

    # Back on the primary: the original title and the tag are visible again.
    doc = _get_doc(client, CONV_MINIMAL)
    assert "# Synthetic minimal conversation" in doc
    assert _frontmatter(doc)["project"] == '"Synthetic Project A"'


# ---------------------------------------------------------------------------
# 14. Content types and Content-Disposition
# ---------------------------------------------------------------------------


def test_single_export_content_type_and_disposition(client) -> None:
    r = _get_export(client, CONV_MINIMAL)
    assert r.content_type == "text/markdown; charset=utf-8"
    assert r.headers["Content-Disposition"].startswith('attachment; filename="')
    assert r.headers["Content-Disposition"].endswith('.md"')


def test_bulk_export_content_type_and_disposition(client) -> None:
    r = _post_bulk(client, [CONV_MINIMAL])
    assert r.content_type == "application/zip"
    assert r.headers["Content-Disposition"].startswith('attachment; filename="')
    assert r.headers["Content-Disposition"].endswith('.zip"')


# ---------------------------------------------------------------------------
# Serializer unit details
# ---------------------------------------------------------------------------


def test_yaml_title_escaping() -> None:
    conv = _linear_conv(
        "ex-quote",
        'He said "hi" \\ done',
        [_mk_msg("ex-q-1", "human", None, [TextBlock("x")])],
    )
    doc = serialise_conversation(conv, conv.canonical_path)
    assert 'title: "He said \\"hi\\" \\\\ done"' in doc


def test_thinking_default_is_off_in_options_dataclass() -> None:
    options = ExportOptions()
    assert options.include_thinking is False
    assert options.include_attachment_text is True


def test_document_ends_with_single_trailing_newline(client) -> None:
    doc = _get_doc(client, CONV_MINIMAL)
    assert doc.endswith(".\n") or doc.endswith("\n")
    assert not doc.endswith("\n\n")


# ---------------------------------------------------------------------------
# Summary frontmatter (v1.2.5 Session O, B4 — operator-ratified: no toggle)
# ---------------------------------------------------------------------------


def test_summary_present_only_when_conversation_has_one(client) -> None:
    """The field rides directly after ``title`` when the conversation has a
    summary; a summary-less conversation carries no field at all."""
    fields = _frontmatter(_get_doc(client, CONV_MINIMAL))
    assert fields["summary"] == '"One human turn, one assistant turn."'
    assert list(fields)[:3] == ["title", "summary", "uuid"]
    # CONV_FULL is hand-built with summary="" — the field order pin in
    # test_frontmatter_full_fields_on_fully_featured_conversation already
    # proves absence there; restate the absence directly for clarity.
    assert "summary" not in _frontmatter(_get_doc(client, CONV_FULL))


def test_summary_omitted_from_copy_output(client) -> None:
    """Copy fetches with ``frontmatter=0`` — the summary is metadata only,
    so the copied text carries no trace of it (the operator's copy
    intuition: what you copy is what the thread says)."""
    doc = _get_doc(client, CONV_MINIMAL, "?frontmatter=0")
    assert "summary" not in doc
    assert "One human turn" not in doc


def test_multiline_summary_roundtrips_as_valid_yaml() -> None:
    """The double-quoted scalar ``_yaml_quote`` emits uses only the ``\\\\``,
    ``\\"``, and ``\\n`` escapes — the JSON-compatible subset of YAML's
    double-quoted style — so ``json.loads`` is a faithful valid-YAML
    round-trip check without adding a YAML dependency."""
    summary = 'Line one.\nLine "two" mentions a \\ backslash.\nLine three.'
    conv = dataclasses.replace(
        _linear_conv(
            "ex-sum",
            "Summary round-trip",
            [_mk_msg("ex-sum-1", "human", None, [TextBlock("x")])],
        ),
        summary=summary,
    )
    doc = serialise_conversation(conv, conv.canonical_path)
    line = next(ln for ln in doc.splitlines() if ln.startswith("summary: "))
    scalar = line[len("summary: ") :]
    assert json.loads(scalar) == summary
    # One line in the header: the newlines travel as escapes, never raw.
    assert "\n" not in scalar
