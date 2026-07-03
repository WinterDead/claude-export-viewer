"""Deterministic builder for every test-fixture ZIP under ``tests/fixtures/``.

Run from the project root::

    python tests/fixtures/build_fixtures.py

Produces one happy-path fixture (``synthetic_export.zip``) plus ten error-path
fixtures (``bad_*.zip``) covering each error branch the parser must handle.

Synthetic UUIDs follow the readable pattern ``00000000-0000-0000-0000-0000000000xx``;
no content from any real export is reproduced here. Anyone may regenerate the ZIPs
from this single source.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Synthetic UUIDs — readable so test assertions are easy to follow.
# ---------------------------------------------------------------------------

ACCOUNT_UUID = "00000000-0000-0000-0000-000000000001"
SENTINEL_PARENT = "00000000-0000-4000-8000-000000000000"

PROJECT_A_UUID = "00000000-0000-0000-0000-0000000000a1"
PROJECT_A_DOC_1 = "00000000-0000-0000-0000-0000000000a2"
PROJECT_A_DOC_2 = "00000000-0000-0000-0000-0000000000a3"

PROJECT_B_UUID = "00000000-0000-0000-0000-0000000000b1"
PROJECT_B_DOC_1 = "00000000-0000-0000-0000-0000000000b2"

# Conversation UUIDs and message UUIDs grouped by leading hex char.
CONV_1_UUID = "00000000-0000-0000-0000-0000000000c1"
CONV_1_MSG_HUMAN = "00000000-0000-0000-0000-0000000000c2"
CONV_1_MSG_ASSISTANT = "00000000-0000-0000-0000-0000000000c3"

CONV_2_UUID = "00000000-0000-0000-0000-0000000000d1"
CONV_2_MSG_HUMAN = "00000000-0000-0000-0000-0000000000d2"
CONV_2_MSG_ASSISTANT = "00000000-0000-0000-0000-0000000000d3"

CONV_3_UUID = "00000000-0000-0000-0000-0000000000e1"
CONV_3_MSG_HUMAN = "00000000-0000-0000-0000-0000000000e2"
CONV_3_MSG_ASSISTANT = "00000000-0000-0000-0000-0000000000e3"

# conv-4: branching. f2 is root, f3 and f4 are sibling assistant replies, f5
# follows f3. f5 has the latest created_at, so canonical_path = [f2, f3, f5].
CONV_4_UUID = "00000000-0000-0000-0000-0000000000f1"
CONV_4_MSG_ROOT = "00000000-0000-0000-0000-0000000000f2"
CONV_4_MSG_BRANCH_A = "00000000-0000-0000-0000-0000000000f3"
CONV_4_MSG_BRANCH_B = "00000000-0000-0000-0000-0000000000f4"
CONV_4_MSG_FOLLOWUP_A = "00000000-0000-0000-0000-0000000000f5"

# A UUID that intentionally has no matching project file.
DANGLING_PROJECT_UUID = "00000000-0000-0000-0000-00000000dead"

CONV_TS = "2026-04-05T10:11:30.196242Z"
MSG_TS = "2026-04-05T10:11:30.196242+00:00"
PROJECT_TS = "2026-02-25T10:00:00.000000+00:00"


# ---------------------------------------------------------------------------
# Builders for each top-level file.
# ---------------------------------------------------------------------------


def _user_record() -> dict[str, Any]:
    return {
        "uuid": ACCOUNT_UUID,
        "full_name": "Synthetic User",
        "email_address": "synthetic@example.invalid",
        "verified_phone_number": "",
    }


def _users_json() -> list[dict[str, Any]]:
    return [_user_record()]


def _memories_json() -> list[dict[str, Any]]:
    return [
        {
            "conversations_memory": (
                "**Work context**\n\nSynthetic global memory body for tests. "
                "Markdown is preserved as-is."
            ),
            "project_memories": {
                # Only proj-A has a memory entry. proj-B intentionally omitted
                # so the parser exercises the "Project.memory == None" branch.
                PROJECT_A_UUID: (
                    "**Purpose & context**\n\nSynthetic per-project memory for proj-A."
                ),
            },
            "account_uuid": ACCOUNT_UUID,
        }
    ]


def _project_a() -> dict[str, Any]:
    return {
        "uuid": PROJECT_A_UUID,
        "name": "Synthetic Project A",
        "description": "A non-starter project with prompt template and two docs.",
        "is_private": False,
        "is_starter_project": False,
        "prompt_template": "Long synthetic prompt-template text for Project A.",
        "created_at": PROJECT_TS,
        "updated_at": PROJECT_TS,
        "creator": {"uuid": ACCOUNT_UUID, "full_name": "Synthetic User"},
        "docs": [
            {
                "uuid": PROJECT_A_DOC_1,
                "filename": "doc-1.md",
                "content": "Body of doc 1.",
                "created_at": PROJECT_TS,
            },
            {
                "uuid": PROJECT_A_DOC_2,
                "filename": "doc-2.md",
                "content": "Body of doc 2.",
                "created_at": PROJECT_TS,
            },
        ],
    }


def _project_b() -> dict[str, Any]:
    return {
        "uuid": PROJECT_B_UUID,
        "name": "Synthetic Starter Project B",
        "description": "A starter project with empty prompt template and one doc.",
        "is_private": False,
        "is_starter_project": True,
        "prompt_template": "",
        "created_at": PROJECT_TS,
        "updated_at": PROJECT_TS,
        "creator": {"uuid": ACCOUNT_UUID, "full_name": "Synthetic User"},
        "docs": [
            {
                "uuid": PROJECT_B_DOC_1,
                "filename": "doc-1.md",
                "content": "Body of doc 1.",
                "created_at": PROJECT_TS,
            },
        ],
    }


def _text_block(text: str) -> dict[str, Any]:
    return {
        "type": "text",
        "text": text,
        "citations": [],
        "flags": None,
        "start_timestamp": MSG_TS,
        "stop_timestamp": MSG_TS,
    }


def _thinking_block(thinking: str) -> dict[str, Any]:
    return {
        "type": "thinking",
        "thinking": thinking,
        "summaries": [],
        "signature": None,
        "truncated": False,
        "cut_off": False,
        "alternative_display_type": None,
        "flags": None,
        "start_timestamp": MSG_TS,
        "stop_timestamp": MSG_TS,
    }


def _tool_use_web_search(tool_id: str, query: str) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "web_search",
        "input": {"query": query, "max_text_results": 10},
        "message": "Searching the web…",
        "icon_name": "search",
        "integration_name": None,
        "integration_icon_url": None,
        "context": None,
        "display_content": None,
        "approval_options": None,
        "approval_key": None,
        "is_mcp_app": None,
        "mcp_server_url": None,
        "flags": None,
        "start_timestamp": MSG_TS,
        "stop_timestamp": MSG_TS,
    }


def _tool_result_with_all_inner(tool_id: str) -> dict[str, Any]:
    """A tool_result whose nested content covers every observed inner type."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "name": "web_search",
        "is_error": False,
        "content": [
            {
                "type": "knowledge",
                "title": "Synthetic search hit",
                "url": "https://example.invalid/page",
                "text": "Snippet body.",
                "is_citable": True,
                "is_missing": False,
                "metadata": {
                    "type": "webpage",
                    "site_domain": "example.invalid",
                    "site_name": "Example",
                    "favicon_url": "https://example.invalid/favicon.ico",
                },
                "prompt_context_metadata": {
                    "url": "https://example.invalid/page",
                    "search_provider": "synthetic",
                },
            },
            {
                "type": "text",
                "uuid": "00000000-0000-0000-0000-0000000000d4",
                "text": "Plain text inside a tool_result.",
            },
            {
                "type": "local_resource",
                "uuid": "00000000-0000-0000-0000-0000000000d5",
                "name": "report.csv",
                "file_path": "/tmp/report.csv",
                "mime_type": "text/csv",
            },
            {
                "type": "image",
                "file_uuid": "00000000-0000-0000-0000-0000000000d6",
            },
        ],
        "structured_content": None,
        "meta": None,
        "message": None,
        "display_content": None,
        "integration_name": None,
        "integration_icon_url": None,
        "icon_name": "search",
        "mcp_server_url": None,
        "flags": None,
        "start_timestamp": None,
        "stop_timestamp": None,
    }


def _tool_use_artifact(tool_id: str, artifact_uuid: str) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "artifacts",
        "input": {
            "command": "create",
            "id": "synthetic-artifact-1",
            "type": "text/html",
            "language": "html",
            "title": "Synthetic artifact",
            "content": "<!doctype html><title>x</title><p>hello</p>",
            "source": None,
            "md_citations": [],
            "version_uuid": artifact_uuid,
        },
        "message": "Creating artifact…",
        "icon_name": "wrench",
        "integration_name": None,
        "integration_icon_url": None,
        "context": None,
        "display_content": None,
        "approval_options": None,
        "approval_key": None,
        "is_mcp_app": None,
        "mcp_server_url": None,
        "flags": None,
        "start_timestamp": MSG_TS,
        "stop_timestamp": MSG_TS,
    }


def _msg(
    *,
    uuid: str,
    sender: str,
    parent: str,
    content: list[dict[str, Any]],
    text: str = "",
    attachments: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
    created_at: str = MSG_TS,
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "text": text or "".join(b.get("text", "") for b in content if b.get("type") == "text"),
        "content": content,
        "sender": sender,
        "created_at": created_at,
        "updated_at": created_at,
        "attachments": attachments or [],
        "files": files or [],
        "parent_message_uuid": parent,
    }


def _conv(
    *,
    uuid: str,
    name: str,
    summary: str,
    messages: list[dict[str, Any]],
    created_at: str = CONV_TS,
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "name": name,
        "summary": summary,
        "created_at": created_at,
        "updated_at": created_at,
        "account": {"uuid": ACCOUNT_UUID},
        "chat_messages": messages,
    }


# ---------------------------------------------------------------------------
# Conversation builders for the happy-path fixture.
# ---------------------------------------------------------------------------


def _conv_1_minimal() -> dict[str, Any]:
    # Assistant has a slightly later created_at so anything that picks the
    # leaf-most message by timestamp behaves deterministically (e.g. the
    # bad_orphan_parent fixture, where both messages become roots).
    human_ts = "2026-04-05T10:11:30.000000+00:00"
    assistant_ts = "2026-04-05T10:11:31.000000+00:00"
    return _conv(
        uuid=CONV_1_UUID,
        name="Synthetic minimal conversation",
        summary="One human turn, one assistant turn.",
        messages=[
            _msg(
                uuid=CONV_1_MSG_HUMAN,
                sender="human",
                parent=SENTINEL_PARENT,
                content=[_text_block("hello from human")],
                created_at=human_ts,
            ),
            _msg(
                uuid=CONV_1_MSG_ASSISTANT,
                sender="assistant",
                parent=CONV_1_MSG_HUMAN,
                content=[_text_block("hello from assistant")],
                created_at=assistant_ts,
            ),
        ],
    )


def _conv_2_thinking_and_tools() -> dict[str, Any]:
    tool_id = "toolu_synthetic_search_1"
    return _conv(
        uuid=CONV_2_UUID,
        name="Synthetic thinking and tools",
        summary="Exercises thinking, tool_use, tool_result with every inner block type.",
        messages=[
            _msg(
                uuid=CONV_2_MSG_HUMAN,
                sender="human",
                parent=SENTINEL_PARENT,
                content=[_text_block("Search the web for me.")],
            ),
            _msg(
                uuid=CONV_2_MSG_ASSISTANT,
                sender="assistant",
                parent=CONV_2_MSG_HUMAN,
                content=[
                    _thinking_block("Reasoning about the request."),
                    _tool_use_web_search(tool_id, query="synthetic query"),
                    _tool_result_with_all_inner(tool_id),
                    _text_block("Here is the answer."),
                ],
            ),
        ],
    )


def _conv_3_artifact_and_files() -> dict[str, Any]:
    tool_id = "toolu_synthetic_artifact_1"
    artifact_uuid = "00000000-0000-0000-0000-0000000000e7"
    attachment = {
        "file_name": "notes.txt",
        "file_type": "txt",
        "file_size": 13,
        "extracted_content": "attached body",
    }
    file_ref = {
        "file_uuid": "00000000-0000-0000-0000-0000000000e8",
        "file_name": "screenshot.png",
    }
    return _conv(
        uuid=CONV_3_UUID,
        name="Synthetic artifact, attachment, and file ref",
        summary="Exercises artifact tool_use, inline attachment, and binary file reference.",
        messages=[
            _msg(
                uuid=CONV_3_MSG_HUMAN,
                sender="human",
                parent=SENTINEL_PARENT,
                content=[_text_block("Make me an artifact.")],
                attachments=[attachment],
                files=[file_ref],
            ),
            _msg(
                uuid=CONV_3_MSG_ASSISTANT,
                sender="assistant",
                parent=CONV_3_MSG_HUMAN,
                content=[
                    _tool_use_artifact(tool_id, artifact_uuid=artifact_uuid),
                    _text_block("Done."),
                ],
            ),
        ],
    )


def _conv_4_branching() -> dict[str, Any]:
    """Tree:

        f2 (human, root, t0)
        ├── f3 (assistant, t1)
        │   └── f5 (human follow-up, t3 — latest)
        └── f4 (assistant regenerate, t2)

    Latest leaf is f5 → canonical_path = [f2, f3, f5].
    branch_points = [f2].
    """
    t0 = "2026-04-05T10:00:00.000000+00:00"
    t1 = "2026-04-05T10:01:00.000000+00:00"
    t2 = "2026-04-05T10:02:00.000000+00:00"
    t3 = "2026-04-05T10:03:00.000000+00:00"
    return _conv(
        uuid=CONV_4_UUID,
        name="Synthetic branching conversation",
        summary="Two assistant siblings; later follow-up sits under the earlier one.",
        messages=[
            _msg(
                uuid=CONV_4_MSG_ROOT,
                sender="human",
                parent=SENTINEL_PARENT,
                content=[_text_block("Tell me a joke.")],
                created_at=t0,
            ),
            _msg(
                uuid=CONV_4_MSG_BRANCH_A,
                sender="assistant",
                parent=CONV_4_MSG_ROOT,
                content=[_text_block("Joke A.")],
                created_at=t1,
            ),
            _msg(
                uuid=CONV_4_MSG_BRANCH_B,
                sender="assistant",
                parent=CONV_4_MSG_ROOT,
                content=[_text_block("Joke B (regenerate).")],
                created_at=t2,
            ),
            _msg(
                uuid=CONV_4_MSG_FOLLOWUP_A,
                sender="human",
                parent=CONV_4_MSG_BRANCH_A,
                content=[_text_block("Tell me another like joke A.")],
                created_at=t3,
            ),
        ],
        created_at=t0.replace("+00:00", "Z"),
    )


def _happy_path_files() -> dict[str, bytes]:
    """Return the file-name → bytes mapping for the happy-path archive."""
    return {
        "users.json": json.dumps(_users_json(), indent=2).encode("utf-8"),
        "memories.json": json.dumps(_memories_json(), indent=2).encode("utf-8"),
        "conversations.json": json.dumps(
            [
                _conv_1_minimal(),
                _conv_2_thinking_and_tools(),
                _conv_3_artifact_and_files(),
                _conv_4_branching(),
            ],
            indent=2,
        ).encode("utf-8"),
        f"projects/{PROJECT_A_UUID}.json": json.dumps(_project_a(), indent=2).encode("utf-8"),
        f"projects/{PROJECT_B_UUID}.json": json.dumps(_project_b(), indent=2).encode("utf-8"),
    }


# ---------------------------------------------------------------------------
# Error-fixture builders. Each starts from the happy-path file map and mutates
# exactly one thing, so the tested behaviour is unambiguous.
# ---------------------------------------------------------------------------


def _bad_missing_users() -> dict[str, bytes]:
    files = _happy_path_files()
    del files["users.json"]
    return files


def _bad_missing_conversations() -> dict[str, bytes]:
    files = _happy_path_files()
    del files["conversations.json"]
    return files


def _bad_missing_memories() -> dict[str, bytes]:
    files = _happy_path_files()
    del files["memories.json"]
    return files


def _bad_invalid_json() -> dict[str, bytes]:
    files = _happy_path_files()
    # Truncate conversations.json mid-record so json.load raises.
    truncated = files["conversations.json"][: len(files["conversations.json"]) // 2]
    files["conversations.json"] = truncated
    return files


def _bad_users_multiple() -> dict[str, bytes]:
    files = _happy_path_files()
    second = dict(_user_record())
    second["uuid"] = "00000000-0000-0000-0000-000000000099"
    second["full_name"] = "Second User"
    files["users.json"] = json.dumps([_user_record(), second], indent=2).encode("utf-8")
    return files


def _bad_missing_field() -> dict[str, bytes]:
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    # Drop the required chat_messages field from conv-1.
    del convs[0]["chat_messages"]
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_empty_conversations() -> dict[str, bytes]:
    files = _happy_path_files()
    files["conversations.json"] = json.dumps([], indent=2).encode("utf-8")
    return files


def _bad_unknown_block() -> dict[str, bytes]:
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    # Inject an unknown content block in conv-1's assistant message, alongside
    # a normal text block so the test can confirm the rest still parses.
    convs[0]["chat_messages"][1]["content"].insert(
        0,
        {
            "type": "speculation",
            "text": "Should be skipped with a warning.",
            "flags": None,
            "start_timestamp": MSG_TS,
            "stop_timestamp": MSG_TS,
        },
    )
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_orphan_parent() -> dict[str, bytes]:
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    # Conv-1's assistant message points to a non-existent parent (not the
    # sentinel and not the actual human message uuid).
    convs[0]["chat_messages"][1]["parent_message_uuid"] = "00000000-0000-0000-0000-0000000000ff"
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_multiple_roots() -> dict[str, bytes]:
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    # Conv-1: replace the assistant message's parent with the sentinel, so the
    # conversation has two messages both claiming to be roots.
    convs[0]["chat_messages"][1]["parent_message_uuid"] = SENTINEL_PARENT
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_sentinel_variants() -> dict[str, bytes]:
    """Conv-1 with four messages, each using a different "rooty" form."""
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    base = convs[0]
    base["name"] = "Sentinel parent variants"
    base["summary"] = "Each root uses a different parent-form; all should normalise to root."

    def root_msg(uuid_suffix: str, parent_form: object, *, omit: bool = False, text: str = "x"):
        m = {
            "uuid": f"00000000-0000-0000-0000-0000000000{uuid_suffix}",
            "text": text,
            "content": [_text_block(text)],
            "sender": "human",
            "created_at": MSG_TS,
            "updated_at": MSG_TS,
            "attachments": [],
            "files": [],
            "parent_message_uuid": parent_form,
        }
        if omit:
            del m["parent_message_uuid"]
        return m

    base["chat_messages"] = [
        root_msg("c4", SENTINEL_PARENT, text="root via sentinel uuid"),
        root_msg("c5", None, text="root via null"),
        root_msg("c6", "", text="root via empty string"),
        root_msg("c7", None, omit=True, text="root via missing field"),
    ]
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_cycle_self() -> dict[str, bytes]:
    """A message whose parent_message_uuid is its own uuid."""
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    convs[0]["name"] = "Self-loop cycle"
    base_msgs = convs[0]["chat_messages"]
    base_msgs[0]["parent_message_uuid"] = base_msgs[0]["uuid"]
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_cycle_two_node() -> dict[str, bytes]:
    """Two messages reference each other; neither has a sentinel parent."""
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    convs[0]["name"] = "Two-node cycle"
    msgs = convs[0]["chat_messages"]
    a = msgs[0]
    b = msgs[1]
    a["parent_message_uuid"] = b["uuid"]
    b["parent_message_uuid"] = a["uuid"]
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_empty_content() -> dict[str, bytes]:
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    # Replace the assistant's content with an empty list and clear text.
    convs[0]["chat_messages"][1]["content"] = []
    convs[0]["chat_messages"][1]["text"] = ""
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_dangling_memory() -> dict[str, bytes]:
    files = _happy_path_files()
    mems = json.loads(files["memories.json"])
    mems[0]["project_memories"][
        DANGLING_PROJECT_UUID
    ] = "**Orphan memory**\n\nReferences a project that no longer exists."
    files["memories.json"] = json.dumps(mems, indent=2).encode("utf-8")
    return files


def _bad_unknown_sender() -> dict[str, bytes]:
    files = _happy_path_files()
    convs = json.loads(files["conversations.json"])
    convs[0]["chat_messages"][0]["sender"] = "system"
    files["conversations.json"] = json.dumps(convs, indent=2).encode("utf-8")
    return files


def _bad_account_mismatch() -> dict[str, bytes]:
    files = _happy_path_files()
    mems = json.loads(files["memories.json"])
    mems[0]["account_uuid"] = "00000000-0000-0000-0000-0000000000ff"
    files["memories.json"] = json.dumps(mems, indent=2).encode("utf-8")
    return files


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------


# The non-zero batch fixture must keep the realistic Anthropic filename pattern
# so the parser's filename-based batch detection can be exercised. Body is a
# clone of the happy-path archive — the defect is in the filename, not contents.
_BATCH_NONZERO_FILENAME = (
    "data-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa-1700000000-deadbeef-batch-0001.zip"
)


# Raw-bytes fixtures — files that are not zip archives at all, for the
# graceful-startup fallthrough (v1.1.5 Session I). Deterministic synthetic
# bytes, deliberately without the "PK" zip magic so zipfile rejects them.
RAW_FIXTURES: dict[str, bytes] = {
    "corrupt_export.zip": b"This file is deliberately not a zip archive.\n" * 8,
}


FIXTURES: dict[str, callable[[], dict[str, bytes]]] = {
    "synthetic_export.zip": _happy_path_files,
    "bad_missing_users.zip": _bad_missing_users,
    "bad_missing_conversations.zip": _bad_missing_conversations,
    "bad_missing_memories.zip": _bad_missing_memories,
    "bad_invalid_json.zip": _bad_invalid_json,
    "bad_users_multiple.zip": _bad_users_multiple,
    "bad_missing_field.zip": _bad_missing_field,
    "bad_empty_conversations.zip": _bad_empty_conversations,
    "bad_unknown_block.zip": _bad_unknown_block,
    "bad_orphan_parent.zip": _bad_orphan_parent,
    "bad_multiple_roots.zip": _bad_multiple_roots,
    "bad_sentinel_variants.zip": _bad_sentinel_variants,
    "bad_cycle_self.zip": _bad_cycle_self,
    "bad_cycle_two_node.zip": _bad_cycle_two_node,
    "bad_empty_content.zip": _bad_empty_content,
    "bad_dangling_memory.zip": _bad_dangling_memory,
    "bad_unknown_sender.zip": _bad_unknown_sender,
    "bad_account_mismatch.zip": _bad_account_mismatch,
    _BATCH_NONZERO_FILENAME: _happy_path_files,
}


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    """Write ``files`` to ``path`` deterministically (fixed mtime, sorted names).

    Determinism keeps the resulting binaries byte-stable across runs so they
    diff cleanly in git. ZipInfo sets a fixed timestamp; entries are sorted by
    name; ZIP_STORED avoids platform-specific deflate variations.
    """
    fixed_date_time = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(filename=name, date_time=fixed_date_time)
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name])


def build_all() -> list[Path]:
    written: list[Path] = []
    for filename, builder in FIXTURES.items():
        out = FIXTURES_DIR / filename
        _write_zip(out, builder())
        written.append(out)
    for filename, raw in RAW_FIXTURES.items():
        out = FIXTURES_DIR / filename
        out.write_bytes(raw)
        written.append(out)
    return written


def main() -> None:
    written = build_all()
    print(f"Wrote {len(written)} fixtures to {FIXTURES_DIR}:")
    for p in written:
        size = p.stat().st_size
        print(f"  {p.name:<35} {size:>6} bytes")


if __name__ == "__main__":
    main()
