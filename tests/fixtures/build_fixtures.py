"""Deterministic builder for every test fixture under ``tests/fixtures/``.

Run from the project root::

    python tests/fixtures/build_fixtures.py

Produces the legacy-container fixtures (the happy-path ``synthetic_export.zip``
plus the error-path ``bad_*.zip`` archives) and, since v1.3.0, the
current-format fixture sets — directories of manifest + category ZIPs
(``current/``, ``current_two_part/``, ``current_dangling/``). The current
sets reuse the legacy builders' records wherever the formats overlap, so the
two containers stay behaviourally parallel (same UUIDs, same message counts)
and dual-format test parametrisation needs no per-format expectations.

Synthetic UUIDs follow the readable pattern ``00000000-0000-0000-0000-0000000000xx``;
IP addresses come only from the RFC 5737 documentation ranges and domains from
RFC 2606. No content from any real export is reproduced here. Anyone may
regenerate every fixture from this single source; the regeneration-guard test
byte-compares the checked-in binaries against a fresh build.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
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
# Current-format fixture sets (v1.3.0). A "set" is a directory of outputs:
# a manifest .json plus category ZIPs, mirroring the manifest-plus-category
# delivery documented in docs/export-schema.md.
# ---------------------------------------------------------------------------

# The memories category's single file is named by an account-scoped UUID.
MEMORY_FILE_UUID = "00000000-0000-0000-0000-00000000f11e"

# Distinctive sentinel standing in for a thinking-block signature blob. The
# deliberate-drop test asserts these bytes never reach the model or the parse
# cache, so the value must appear nowhere else in any fixture.
SIGNATURE_SENTINEL = "U1lOVEhFVElDLVNJR05BVFVSRS1TRU5USU5FTA=="

# Manifest filename follows the observed pattern:
# manifest-<job_uuid>-<unix_timestamp>-<short_hash>-<YYYY-MM-DD-hh-mm-ss>.json
MANIFEST_FILENAME = (
    "manifest-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa-1700000000-deadbeef-2026-08-25-05-00-00.json"
)

# Distinct per-file timestamps so updated_at ordering is testable.
MEMFILE_TS = {
    "/profile.md": "2026-08-05T09:00:00.000000+00:00",
    "/people/synthetic-colleague.md": "2026-08-12T14:30:00.000000+00:00",
    "/topics/nested/deeper-topic.md": "2026-08-18T11:45:00.000000+00:00",
    "/preferences.md": "2026-08-22T16:20:00.000000+00:00",
}


def _to_z_form(value: Any) -> Any:
    """Rewrite explicit-offset timestamps to the ``Z`` form the current
    format uses throughout ``conversations.json`` (schema doc, "Timestamp
    forms by file")."""
    if isinstance(value, str) and value.endswith("+00:00") and "T" in value:
        return value[: -len("+00:00")] + "Z"
    if isinstance(value, list):
        return [_to_z_form(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_z_form(v) for k, v in value.items()}
    return value


def _injected_prompt_block() -> dict[str, Any]:
    """The current-only block type, with its full trio of fields."""
    return {
        "type": "injected_prompt_block",
        "prompt": "Synthetic injected instruction text from a product feature.",
        "initial_turn_only": True,
        "skip_on_truncated_continuation": False,
        "flags": None,
        "start_timestamp": MSG_TS,
        "stop_timestamp": MSG_TS,
    }


def _thinking_block_current(
    thinking: str,
    *,
    summaries: tuple[str, ...] = (),
    signature: str | None = None,
    thinking_hidden: bool = False,
) -> dict[str, Any]:
    block = _thinking_block(thinking)
    block["summaries"] = [{"summary": s} for s in summaries]
    block["signature"] = signature
    block["hidden"] = False
    block["thinking_hidden"] = thinking_hidden
    return block


def _current_tool_fields() -> dict[str, Any]:
    """Fields present on tool blocks only in the current format."""
    return {
        "approval_key_legacy": None,
        "hidden_in_chat": None,
        "tool_identifier": None,
        "tool_origin": "first_party",
    }


def _link_dict(title: str, url: str) -> dict[str, Any]:
    return {
        "icon_url": None,
        "resource_type": "webpage",
        "source": "Example",
        "subtitles": None,
        "title": title,
        "url": url,
    }


def _tool_use_current(
    tool_id: str, name: str, display_content: dict[str, Any] | None
) -> dict[str, Any]:
    block = _tool_use_web_search(tool_id, query="unused")
    block["name"] = name
    block["input"] = {"query": "synthetic follow-up"}
    block["display_content"] = display_content
    block.update(_current_tool_fields())
    return block


def _tool_result_current(
    tool_id: str,
    name: str,
    inner: list[dict[str, Any]],
    *,
    display_content: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "name": name,
        "is_error": False,
        "content": inner,
        "structured_content": None,
        "meta": meta,
        "message": None,
        "display_content": display_content,
        "integration_name": None,
        "integration_icon_url": None,
        "icon_name": "wrench",
        "mcp_server_url": None,
        "hidden_in_chat": None,
        "flags": None,
        "start_timestamp": None,
        "stop_timestamp": None,
    }


def _current_conversations() -> list[dict[str, Any]]:
    """The four legacy conversations upgraded with current-format block
    fields — same UUIDs and message counts, so count-level expectations hold
    across both container formats. Covers: the injected-prompt trio, thinking
    with/without summaries and with ``thinking_hidden``, a signature value,
    text-block citations, all six ``display_content`` types, knowledge
    ``links``, and ``meta.output_format_category: md``.
    """
    conv_1 = _conv_1_minimal()
    conv_2 = _conv_2_thinking_and_tools()
    conv_3 = _conv_3_artifact_and_files()
    conv_4 = _conv_4_branching()

    # conv-1: injected prompt ahead of the human's text block.
    conv_1["chat_messages"][0]["content"].insert(0, _injected_prompt_block())

    # conv-2 assistant message: [thinking, tool_use, tool_result, text].
    asst = conv_2["chat_messages"][1]["content"]
    thinking, search_use, search_result, final_text = asst

    thinking["summaries"] = [
        {"summary": "Considered what the request needs."},
        {"summary": "Planned a web search."},
    ]
    thinking["signature"] = SIGNATURE_SENTINEL
    thinking["hidden"] = False
    thinking["thinking_hidden"] = False

    search_use["display_content"] = {"type": "text", "text": "Searched the web"}
    search_use.update(_current_tool_fields())

    search_result["display_content"] = {
        "type": "table",
        "table": [["Name", "Value"], ["Alpha", "1"], ["Beta", "2"]],
    }
    search_result["hidden_in_chat"] = None
    # Knowledge links — current-only field on the inner knowledge block.
    search_result["content"][0]["links"] = [
        {"title": "Related page", "url": "https://example.invalid/related"}
    ]

    final_text["citations"] = [
        {
            "uuid": "00000000-0000-0000-0000-00000000ce01",
            "start_index": 0,
            "end_index": 4,
            "details": {"type": "web_search_citation", "url": "https://example.invalid/cited-1"},
        },
        {
            "uuid": "00000000-0000-0000-0000-00000000ce02",
            "start_index": 12,
            "end_index": 18,
            "details": {"type": "web_search_citation", "url": "https://example.invalid/cited-2"},
        },
    ]
    final_text["citations_grouping_mode"] = "combine"

    pair2 = "toolu_synthetic_follow_1"
    pair3 = "toolu_synthetic_mcp_1"
    pair4 = "toolu_synthetic_view_1"
    conv_2["chat_messages"][1]["content"] = [
        thinking,
        _thinking_block_current("Second reasoning pass without summaries."),
        _thinking_block_current(
            "Reasoning the product marked hidden.",
            summaries=("Summary of the hidden pass.",),
            thinking_hidden=True,
        ),
        search_use,
        search_result,
        _tool_use_current(
            pair2,
            "web_fetch",
            {
                "type": "rich_link",
                "link": _link_dict("Synthetic linked resource", "https://example.invalid/linked"),
                "is_trusted": False,
            },
        ),
        _tool_result_current(
            pair2,
            "web_fetch",
            [{"type": "text", "text": "Fetched page body."}],
            display_content={"type": "json_block", "json_block": '{"result": "ok", "items": 2}'},
        ),
        _tool_use_current(
            pair3,
            "synthetic-server:lookup",
            {
                "type": "rich_content",
                "content": [
                    _link_dict("First rich item", "https://example.invalid/rich-1"),
                    _link_dict("Second rich item", "https://example.invalid/rich-2"),
                ],
            },
        ),
        _tool_result_current(
            pair3,
            "synthetic-server:lookup",
            [{"type": "text", "text": "**Bold** markdown result with `code`."}],
            meta={"output_format_category": "md"},
        ),
        # A document-sized display body (amendment A5): renders pre-collapsed,
        # teased by the result's own message line.
        _tool_use_current(pair4, "view", {"type": "text", "text": "Reading a file"}),
        _view_skill_result(pair4),
        final_text,
    ]

    # conv-3: the artifact call displayed as a code block.
    conv_3["chat_messages"][1]["content"][0]["display_content"] = {
        "type": "code_block",
        "code": "<!doctype html><title>x</title><p>hello</p>",
        "filename": None,
        "language": "html",
    }
    conv_3["chat_messages"][1]["content"][0].update(_current_tool_fields())

    # conv-3 (v1.4.0): two attached-document blocks on the human turn (one
    # without a title), and the assistant publishing the multi-file artifact
    # (structured_content.artifact_id) and editing the page artifact through
    # a Docs-style connector (input.container.id) — record §6.5, V17–V19.
    conv_3["chat_messages"][0]["content"].extend(
        [
            _document_block("Synthetic brief.pdf"),
            _document_block(None),
        ]
    )
    publish_id = "toolu_synthetic_publish_1"
    docs_id = "toolu_synthetic_docs_1"
    assistant = conv_3["chat_messages"][1]["content"]
    assistant[1:1] = [
        _artifact_publish_use(publish_id),
        _artifact_publish_result(publish_id),
        _docs_update_use(docs_id),
        _tool_result_current(
            docs_id,
            "docs_update",
            [{"type": "text", "text": "Updated the synthetic page."}],
        ),
    ]

    return [conv_1, conv_2, conv_3, conv_4]


def _document_block(title: str | None) -> dict[str, Any]:
    """An attached-document block (V18): the file itself is never exported."""
    block: dict[str, Any] = {
        "type": "document",
        "file_uuid": (
            "00000000-0000-0000-0000-0000000000d0"
            if title
            else ("00000000-0000-0000-0000-0000000000d9")
        ),
        "flags": None,
        "start_timestamp": MSG_TS,
        "stop_timestamp": MSG_TS,
    }
    if title is not None:
        block["title"] = title
    return block


def _artifact_publish_use(tool_id: str) -> dict[str, Any]:
    block = _tool_use_current(tool_id, "Artifact", None)
    block["input"] = {"action": "publish", "title": "Synthetic reading log"}
    return block


def _artifact_publish_result(tool_id: str) -> dict[str, Any]:
    result = _tool_result_current(
        tool_id,
        "Artifact",
        [{"type": "text", "text": f"Published artifact {ARTIFACT_MULTI_UUID}."}],
        meta={"tool_references": ["Artifact"]},
    )
    result["structured_content"] = {
        "artifact_id": ARTIFACT_MULTI_UUID,
        "url": f"{ARTIFACT_URL_BASE}{ARTIFACT_MULTI_UUID}",
    }
    return result


def _docs_update_use(tool_id: str) -> dict[str, Any]:
    block = _tool_use_current(tool_id, "docs_update", None)
    block["input"] = {
        "container": {"kind": "project", "id": ARTIFACT_PAGE_UUID},
        "payload": "Synthetic edit text.",
    }
    return block


SKILL_FILE_LINES = 42
SKILL_FILE_MESSAGE = "Read the synthetic skill file"


def _view_skill_result(tool_id: str) -> dict[str, Any]:
    """A view-tool result whose display body is a whole synthetic file."""
    body = "\n".join(
        f"Line {n:02d} of a synthetic skill file used to exercise size collapse."
        for n in range(1, SKILL_FILE_LINES + 1)
    )
    result = _tool_result_current(
        tool_id,
        "view",
        [{"type": "text", "text": body}],
        display_content={"type": "text", "text": body},
    )
    result["message"] = SKILL_FILE_MESSAGE
    return result


def _conversations_zip(convs: list[dict[str, Any]]) -> dict[str, bytes]:
    return {
        "conversations.json": json.dumps(_to_z_form(convs), indent=2).encode("utf-8"),
    }


def _current_conversations_files() -> dict[str, bytes]:
    return _conversations_zip(_current_conversations())


def _current_projects_files() -> dict[str, bytes]:
    """Projects are field-identical across formats; only the container moves."""
    return {
        f"projects/{PROJECT_A_UUID}.json": json.dumps(_project_a(), indent=2).encode("utf-8"),
        f"projects/{PROJECT_B_UUID}.json": json.dumps(_project_b(), indent=2).encode("utf-8"),
    }


def _current_memories() -> dict[str, Any]:
    """The current memories shape: a dict (not the legacy list-of-1), with
    the legacy fields plus the new memory system's ``memory_files``. Covers
    a ``/people/`` path, deeper nesting, and one file without frontmatter."""
    legacy = _memories_json()[0]
    return {
        "account_uuid": ACCOUNT_UUID,
        "conversations_memory": legacy["conversations_memory"],
        "project_memories": dict(legacy["project_memories"]),
        "memory_files": [
            {
                "path": "/profile.md",
                "content": "---\nname: profile\n---\n\nSynthetic profile body with **markdown**.",
                "updated_at": MEMFILE_TS["/profile.md"],
            },
            {
                "path": "/people/synthetic-colleague.md",
                "content": (
                    "---\nname: synthetic-colleague\nrelation: coworker\n---\n\n"
                    "Notes about a synthetic colleague."
                ),
                "updated_at": MEMFILE_TS["/people/synthetic-colleague.md"],
            },
            {
                "path": "/topics/nested/deeper-topic.md",
                "content": "---\nname: deeper-topic\n---\n\nA memory file nested two levels deep.",
                "updated_at": MEMFILE_TS["/topics/nested/deeper-topic.md"],
            },
            {
                "path": "/preferences.md",
                "content": "Synthetic preference notes without a frontmatter fence.",
                "updated_at": MEMFILE_TS["/preferences.md"],
            },
        ],
    }


def _memories_zip(memories: dict[str, Any]) -> dict[str, bytes]:
    return {
        f"memories/{MEMORY_FILE_UUID}.json": json.dumps(memories, indent=2).encode("utf-8"),
    }


def _current_memories_files() -> dict[str, bytes]:
    return _memories_zip(_current_memories())


def _current_memories_dangling_files() -> dict[str, bytes]:
    """Swap-in memories variant carrying a dangling project memory — the
    current-format twin of ``bad_dangling_memory.zip``."""
    memories = _current_memories()
    memories["project_memories"][
        DANGLING_PROJECT_UUID
    ] = "**Orphan memory**\n\nReferences a project that no longer exists."
    return _memories_zip(memories)


def _login_history() -> dict[str, Any]:
    """Three events, deliberately unordered (newest is the middle entry) so
    the Account page's newest-first sort is testable. IPs are drawn from the
    RFC 5737 documentation ranges only."""

    def event(ip: str, ts: str, country: str, ua: dict[str, Any]) -> dict[str, Any]:
        return {
            "account_uuid": ACCOUNT_UUID,
            "ip_address": ip,
            "timestamp": ts,
            "method": "magic_link",
            "location_info": {"city": None, "region": None, "country": country},
            "user_agent": ua,
        }

    return {
        "login_events": [
            event(
                "203.0.113.10",
                "2026-08-01T10:00:00.000000+00:00",
                "US",
                {
                    "browser_family": "Firefox",
                    "browser_version": "142.0",
                    "device_family": "Other",
                    "os_family": "Windows",
                    "os_version": "11",
                },
            ),
            event(
                "198.51.100.7",
                "2026-08-20T08:30:00.000000+00:00",
                "CA",
                {
                    "browser_family": "Chrome",
                    "browser_version": "127.0",
                    "device_family": "Other",
                    "os_family": "Linux",
                    "os_version": None,
                },
            ),
            event(
                "192.0.2.55",
                "2026-08-10T21:15:00.000000+00:00",
                "US",
                {
                    "browser_family": "Mobile Safari",
                    "browser_version": "17.5",
                    "device_family": "iPhone",
                    "os_family": "iOS",
                    "os_version": "17.5",
                },
            ),
        ]
    }


def _current_light_metadata_files() -> dict[str, bytes]:
    return {
        "users.json": json.dumps(_users_json(), indent=2).encode("utf-8"),
        "login_history.json": json.dumps(_login_history(), indent=2).encode("utf-8"),
    }


def _current_manifest_bytes() -> bytes:
    # The full eight-file delivery (v1.3.1): the manifest plus seven
    # category ZIPs, in the order observed in real manifests.
    categories = [
        "light_metadata",
        "projects",
        "memories",
        "frames",
        "artifact_data",
        "claude_code_cloud",
        "conversations",
    ]
    manifest = {
        "version": "1.0",
        "created_at": "2026-08-25T05:00:00.000000+00:00",
        "total_files": len(categories),
        "instructions": "Download each file listed below.",
        "data_files": [
            {
                "batch_index": i,
                "category": cat,
                "part": 0,
                "filename": f"{cat}-000.zip",
                "export_url": f"https://example.invalid/download/{cat}-000.zip",
            }
            for i, cat in enumerate(categories)
        ],
    }
    return json.dumps(manifest, indent=2).encode("utf-8")


def _current_conversations_part0_files() -> dict[str, bytes]:
    convs = _current_conversations()
    return _conversations_zip(convs[:2])


def _current_conversations_part1_files() -> dict[str, bytes]:
    """Part 1 carries the remaining conversations plus a mutated duplicate of
    conv-1 (same UUID, different name) — the multi-part merge must keep the
    part-0 copy once and flag the duplicate."""
    convs = _current_conversations()
    duplicate = _current_conversations()[0]
    duplicate["name"] = "Synthetic minimal conversation (duplicate part)"
    return _conversations_zip([*convs[2:], duplicate])


# ---------------------------------------------------------------------------
# The three later categories (v1.4.0 Session U): frames (published artifacts),
# artifact_data, and claude_code_cloud. Structures mirror the verification
# table in docs/export-categories-design.md §2; every name, title, path, and
# body below is synthetic.
# ---------------------------------------------------------------------------

ARTIFACT_SINGLE_UUID = "00000000-0000-0000-0000-0000000a0001"
ARTIFACT_MULTI_UUID = "00000000-0000-0000-0000-0000000a0002"
ARTIFACT_PAGE_UUID = "00000000-0000-0000-0000-0000000a0003"
# Artifact data whose artifact is not in any frames file (record §6.4 orphans).
ARTIFACT_ORPHAN_DATA_UUID = "00000000-0000-0000-0000-0000000a00ff"

# Edge-set artifacts (record §10).
ARTIFACT_INTEGRITY_UUID = "00000000-0000-0000-0000-0000000a0e01"
ARTIFACT_NO_VERSIONS_UUID = "00000000-0000-0000-0000-0000000a0e02"
ARTIFACT_ACTIVE_MISSING_UUID = "00000000-0000-0000-0000-0000000a0e03"
ARTIFACT_UNKNOWN_KIND_UUID = "00000000-0000-0000-0000-0000000a0e04"
ARTIFACT_BAD_COMMENTS_UUID = "00000000-0000-0000-0000-0000000a0e05"

ROUTINE_CRON_ID = "trig_01SYNTHETICCRON00000001"
ROUTINE_ONCE_ID = "trig_01SYNTHETICONCE00000002"
ROUTINE_ABSENT_ID = "trig_01SYNTHETICGONE00000003"

ARTIFACT_URL_BASE = "https://example.invalid/artifact/"
FONT_URL = "https://fonts.example.invalid/css2?family=Synthetic+Sans"


def _iso(seconds: int) -> str:
    # artifact.json timestamps use the explicit-offset form (V6).
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _version(seconds: int, suffix: str, title: str, description: str) -> dict[str, Any]:
    """A versions[] entry whose id embeds its created_at seconds (V6)."""
    return {
        "id": f"{seconds}-{suffix}",
        "title": title,
        "description": description,
        "created_at": _iso(seconds),
    }


def _artifact_json(
    artifact_id: str,
    kind: str,
    versions: list[dict[str, Any]],
    active: str | None,
    *,
    visibility: str = "private",
    updated_at: str | None = None,
) -> bytes:
    record = {
        "id": artifact_id,
        "kind": kind,
        "visibility": visibility,
        "owner_account": ACCOUNT_UUID,
        "updated_at": updated_at or (versions[0]["created_at"] if versions else _iso(1788000000)),
        "active_version": active,
        "versions": versions,
    }
    return json.dumps(record, indent=2).encode("utf-8")


SINGLE_VERSION = _version(1788100000, "a1b2", "Synthetic colour card", "A one-page card.")

SINGLE_HTML = (
    "<!doctype html>\n"
    '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    "<title>Synthetic colour card</title>\n"
    f'<link rel="stylesheet" href="{FONT_URL}">\n'
    "<style>\n"
    "  body { font-family: 'Synthetic Sans', sans-serif; background: #ffffff; color: #1a1a1a; }\n"
    "  @media (prefers-color-scheme: dark) {\n"
    "    body { background: #161616; color: #eeeeee; }\n"
    "  }\n"
    "</style>\n</head>\n<body>\n"
    "<h1>Synthetic colour card</h1>\n"
    "<p>A static page with no scripts, styled for light and dark.</p>\n"
    "</body>\n</html>\n"
)

# Three versions listed out of chronological order; the active one is the
# middle version, not the newest (V7).
MULTI_VERSIONS = [
    _version(1789500000, "c3d4", "Synthetic reading log", "Second draft — the active one."),
    _version(1790000000, "e5f6", "Synthetic reading log", "Newest draft, not yet active."),
    _version(1789000000, "a7b8", "Synthetic reading log", "First draft."),
]
MULTI_ACTIVE = MULTI_VERSIONS[0]["id"]

# A few bytes standing in for a woff2 font — never decoded, only served.
FAKE_WOFF2 = b"wOF2\x00\x01\x00\x00synthetic-font-bytes"

CAPABILITY_JSON = {
    "v": 1,
    "source": "db",
    "db": {"meta": {"title": "Synthetic reading log"}, "collections": [{"name": "log_entries"}]},
}


def _multi_entry_html(label: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>Synthetic reading log</title>\n"
        '<link rel="stylesheet" href="assets/style.css">\n'
        "</head>\n<body>\n"
        f'<main id="app"><h1>Synthetic reading log</h1><p class="draft">{label}</p></main>\n'
        "<script>document.getElementById('app').dataset.scriptRan = 'yes';</script>\n"
        "</body>\n</html>\n"
    )


MULTI_CSS = (
    "@font-face { font-family: 'Synthetic Mono'; src: url('font.woff2') format('woff2'); }\n"
    "body { font-family: 'Synthetic Mono', monospace; }\n"
    ".draft { color: #2a6fbb; }\n"
)


def _multi_version_files(label: str) -> list[tuple[str, str, bytes]]:
    """``(path, mime_type, bytes)`` for one multi-file version (V8, V9, V13)."""
    return [
        ("index.html", "text/html", _multi_entry_html(label).encode("utf-8")),
        ("assets/style.css", "text/css", MULTI_CSS.encode("utf-8")),
        ("assets/font.woff2", "font/woff2", FAKE_WOFF2),
        (
            "content/entries.md",
            "text/markdown",
            (
                "# Reading log\n\n- First synthetic entry\n" f"- Second synthetic entry ({label})\n"
            ).encode(),
        ),
        (
            "content/about.md",
            "text/markdown",
            b"## About\n\nThis log is entirely synthetic test content.\n",
        ),
        (
            "capability.json",
            "application/json",
            json.dumps(CAPABILITY_JSON, indent=2).encode("utf-8"),
        ),
    ]


def _files_json(files: list[tuple[str, str, bytes]]) -> bytes:
    return json.dumps(
        {
            "files": [
                {
                    "original_path": path,
                    "exported_as": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "mime_type": mime,
                }
                for path, mime, data in files
            ]
        },
        indent=2,
    ).encode("utf-8")


def _multi_file_members(
    artifact_id: str, version_id: str, files: list[tuple[str, str, bytes]]
) -> dict[str, bytes]:
    base = f"artifacts/{artifact_id}/versions/{version_id}"
    members = {f"{base}.files.json": _files_json(files)}
    for path, _mime, data in files:
        members[f"{base}/{path}"] = data
    return members


PAGE_VERSION = _version(1788800000, "9d0e", "Synthetic shared notes", "A document page.")

PAGE_MARKDOWN = (
    "# Synthetic shared notes\n\n"
    "A document-kind artifact whose content is this Markdown file.\n\n"
    "- First point\n- Second point with **emphasis**\n"
)

PAGE_COMMENTS = [
    {
        "resolved": True,
        "tab": "main",
        "quoted_text": "First point",
        "comments": [
            {"body": "Synthetic resolved remark.", "created_at": "2026-09-10T08:00:00.120Z"},
            {
                "body": "Synthetic reply closing the thread.",
                "created_at": "2026-09-10T09:00:00.480Z",
            },
        ],
    },
    {
        "resolved": False,
        "tab": "main",
        "quoted_text": "Second point",
        "comments": [
            {"body": "Synthetic open question.", "created_at": "2026-09-11T10:30:00.007Z"},
        ],
    },
]


def _single_artifact_members() -> dict[str, bytes]:
    base = f"artifacts/{ARTIFACT_SINGLE_UUID}"
    return {
        f"{base}/artifact.json": _artifact_json(
            ARTIFACT_SINGLE_UUID, "artifact", [SINGLE_VERSION], SINGLE_VERSION["id"]
        ),
        f"{base}/versions/{SINGLE_VERSION['id']}.html": SINGLE_HTML.encode("utf-8"),
    }


def _multi_artifact_members() -> dict[str, bytes]:
    base = f"artifacts/{ARTIFACT_MULTI_UUID}"
    members = {
        f"{base}/artifact.json": _artifact_json(
            ARTIFACT_MULTI_UUID,
            "artifact",
            MULTI_VERSIONS,
            MULTI_ACTIVE,
            updated_at=MULTI_VERSIONS[1]["created_at"],
        )
    }
    for version in MULTI_VERSIONS:
        members.update(
            _multi_file_members(
                ARTIFACT_MULTI_UUID,
                version["id"],
                _multi_version_files(version["description"]),
            )
        )
    return members


def _page_artifact_members() -> dict[str, bytes]:
    base = f"artifacts/{ARTIFACT_PAGE_UUID}"
    return {
        f"{base}/artifact.json": _artifact_json(
            ARTIFACT_PAGE_UUID, "page", [PAGE_VERSION], PAGE_VERSION["id"]
        ),
        f"{base}/shared-notes.md": PAGE_MARKDOWN.encode("utf-8"),
        f"{base}/comments.json": json.dumps(PAGE_COMMENTS, indent=2).encode("utf-8"),
    }


def _frames_files() -> dict[str, bytes]:
    return {
        **_single_artifact_members(),
        **_multi_artifact_members(),
        **_page_artifact_members(),
    }


def _data_file(collection: str, documents: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        {"documents": [{"collection": collection, **doc} for doc in documents]}, indent=2
    ).encode("utf-8")


def _artifact_data_files() -> dict[str, bytes]:
    return {
        f"artifacts/{ARTIFACT_MULTI_UUID}/data/entries.json": _data_file(
            "log_entries",
            [
                {
                    "id": "entry-0001",
                    "created_at": "2026-09-12T12:00:00.000000+00:00",
                    "updated_at": "2026-09-12T12:00:00.000000+00:00",
                    "data": {"book": "Synthetic Title One", "page_count": 120, "finished": True},
                },
                {
                    "id": "entry-0002",
                    "created_at": "2026-09-13T12:00:00.000000+00:00",
                    "updated_at": "2026-09-14T08:00:00.000000+00:00",
                    "data": {"book": "Synthetic Title Two", "page_count": 45, "tags": ["a", "b"]},
                },
            ],
        ),
        f"artifacts/{ARTIFACT_MULTI_UUID}/data/settings.json": _data_file(
            "log_settings",
            [
                {
                    "id": "prefs",
                    "created_at": "2026-09-12T12:00:00.000000+00:00",
                    "updated_at": "2026-09-12T12:00:00.000000+00:00",
                    "data": {"theme": "dark"},
                }
            ],
        ),
        f"artifacts/{ARTIFACT_ORPHAN_DATA_UUID}/data/notes.json": _data_file(
            "notes",
            [
                {
                    "id": "note-1",
                    "created_at": "2026-09-01T07:00:00.000000+00:00",
                    "updated_at": "2026-09-01T07:00:00.000000+00:00",
                    "data": {"text": "Synthetic data whose artifact is not in the export."},
                }
            ],
        ),
    }


def _routines() -> list[dict[str, Any]]:
    return [
        {
            "routine_id": ROUTINE_CRON_ID,
            "agent_id": None,
            "name": "Synthetic weekly tidy-up",
            "prompt": "Review the synthetic repository and summarise open items.",
            "prompt_withheld": False,
            "enabled": True,
            "schedule": {"cron": "0 9 * * 1", "run_once_at": None},
            "created_at": "2026-09-01T09:00:00.250000Z",
            "updated_at": "2026-09-02T09:00:00.250000Z",
            "ended_at": None,
        },
        {
            "routine_id": ROUTINE_ONCE_ID,
            "agent_id": None,
            "name": "Synthetic one-off check",
            "prompt": "",
            "prompt_withheld": True,
            "enabled": False,
            "schedule": {"cron": None, "run_once_at": "2026-09-20T15:30:00Z"},
            "created_at": "2026-09-15T10:00:00.500000Z",
            "updated_at": "2026-09-15T10:00:00.500000Z",
            "ended_at": None,
        },
    ]


def _routine_runs() -> list[dict[str, Any]]:
    def run(routine: str, day: int, session: str) -> dict[str, Any]:
        return {
            "routine_id": routine,
            "scheduled_for": f"2026-09-{day:02d}T09:00:00Z",
            # Run timestamps carry six to nine fractional digits.
            "started_at": f"2026-09-{day:02d}T09:00:04.123456789Z",
            "finished_at": f"2026-09-{day:02d}T09:03:41.654321Z",
            "status": "succeeded",
            "session_id": f"cse_01SYNTHETICSESSION{session}",
        }

    return [
        run(ROUTINE_CRON_ID, 8, "0001"),
        run(ROUTINE_CRON_ID, 15, "0002"),
        # A run whose routine is not in routines.json (record §10, D9 orphans).
        run(ROUTINE_ABSENT_ID, 3, "0003"),
    ]


def _claude_code_cloud_files() -> dict[str, bytes]:
    return {
        "claude_code_cloud/routines.json": json.dumps(
            {"claude_code_cloud": _routines()}, indent=2
        ).encode("utf-8"),
        "claude_code_cloud/routine_runs.json": json.dumps(
            {"claude_code_cloud": _routine_runs()}, indent=2
        ).encode("utf-8"),
    }


def _frames_part0_files() -> dict[str, bytes]:
    return {**_single_artifact_members(), **_page_artifact_members()}


def _frames_part1_files() -> dict[str, bytes]:
    """Part 1 carries the multi-file artifact plus a mutated duplicate of the
    single-HTML artifact (same id, different title) — kept once, flagged."""
    duplicate_version = dict(SINGLE_VERSION, title="Synthetic colour card (duplicate part)")
    base = f"artifacts/{ARTIFACT_SINGLE_UUID}"
    return {
        **_multi_artifact_members(),
        f"{base}/artifact.json": _artifact_json(
            ARTIFACT_SINGLE_UUID, "artifact", [duplicate_version], duplicate_version["id"]
        ),
        f"{base}/versions/{SINGLE_VERSION['id']}.html": SINGLE_HTML.encode("utf-8"),
    }


def _frames_edge_files() -> dict[str, bytes]:
    """Every frames preserve-and-flag case in one set (record §6.1, §10)."""
    members: dict[str, bytes] = {}

    # Integrity: one hash mismatch, one size mismatch, one listed file
    # missing, one file present but unlisted.
    version = _version(1789200000, "0b0b", "Synthetic integrity case", "Damaged files.")
    files = _multi_version_files("integrity")
    listing = json.loads(_files_json(files))
    listing["files"][1]["sha256"] = "0" * 64  # assets/style.css: hash mismatch
    listing["files"][2]["size_bytes"] += 1  # assets/font.woff2: size mismatch
    base = f"artifacts/{ARTIFACT_INTEGRITY_UUID}"
    vbase = f"{base}/versions/{version['id']}"
    members[f"{base}/artifact.json"] = _artifact_json(
        ARTIFACT_INTEGRITY_UUID, "artifact", [version], version["id"]
    )
    members[f"{vbase}.files.json"] = json.dumps(listing, indent=2).encode("utf-8")
    for path, _mime, data in files:
        if path != "content/about.md":  # listed but missing from the export
            members[f"{vbase}/{path}"] = data
    members[f"{vbase}/extra/unlisted.txt"] = b"present but not in the file list\n"

    # Zero versions and no active version.
    members[f"artifacts/{ARTIFACT_NO_VERSIONS_UUID}/artifact.json"] = _artifact_json(
        ARTIFACT_NO_VERSIONS_UUID, "artifact", [], None
    )

    # Active version not in the list; the listed version has no files.
    listed = _version(1789300000, "1c1c", "Synthetic dangling active", "No files exported.")
    members[f"artifacts/{ARTIFACT_ACTIVE_MISSING_UUID}/artifact.json"] = _artifact_json(
        ARTIFACT_ACTIVE_MISSING_UUID, "artifact", [listed], "1789399999-ffff"
    )

    # Unknown kind and visibility values, preserved raw.
    odd = _version(1789400000, "2d2d", "Synthetic future kind", "A kind this viewer predates.")
    base = f"artifacts/{ARTIFACT_UNKNOWN_KIND_UUID}"
    members[f"{base}/artifact.json"] = _artifact_json(
        ARTIFACT_UNKNOWN_KIND_UUID, "widget", [odd], odd["id"], visibility="link_shared"
    )
    members[f"{base}/versions/{odd['id']}.html"] = SINGLE_HTML.encode("utf-8")

    # A page whose comments file is unreadable and which has no content file.
    page = _version(1789450000, "3e3e", "Synthetic broken page", "Unreadable comments.")
    base = f"artifacts/{ARTIFACT_BAD_COMMENTS_UUID}"
    members[f"{base}/artifact.json"] = _artifact_json(
        ARTIFACT_BAD_COMMENTS_UUID, "page", [page], page["id"]
    )
    members[f"{base}/comments.json"] = b'{"this is": not valid JSON'
    return members


def _current_memories_without_files() -> dict[str, bytes]:
    """A current memories file with no memory files — the summary stays the
    export's memory (record §7: not legacy)."""
    memories = _current_memories()
    memories["memory_files"] = []
    return _memories_zip(memories)


def _legacy_with_login_history_files() -> dict[str, bytes]:
    """The legacy happy path plus a root-level login_history.json (V21)."""
    files = _happy_path_files()
    files["login_history.json"] = json.dumps(_login_history(), indent=2).encode("utf-8")
    return files


# Each set entry maps an output filename to its builder; a dict result is
# written as a deterministic zip, raw bytes are written verbatim.
FIXTURE_SETS: dict[str, dict[str, Any]] = {
    "current": {
        MANIFEST_FILENAME: _current_manifest_bytes,
        "conversations-000.zip": _current_conversations_files,
        "projects-000.zip": _current_projects_files,
        "memories-000.zip": _current_memories_files,
        "light_metadata-000.zip": _current_light_metadata_files,
        "frames-000.zip": _frames_files,
        "artifact_data-000.zip": _artifact_data_files,
        "claude_code_cloud-000.zip": _claude_code_cloud_files,
    },
    "current_two_part": {
        "conversations-000.zip": _current_conversations_part0_files,
        "conversations-001.zip": _current_conversations_part1_files,
    },
    "current_dangling": {
        "memories-000.zip": _current_memories_dangling_files,
    },
    # Record §10 edge sets for the three later categories.
    "current_new_only": {
        "frames-000.zip": _frames_files,
        "artifact_data-000.zip": _artifact_data_files,
        "claude_code_cloud-000.zip": _claude_code_cloud_files,
    },
    "current_frames_two_part": {
        "frames-000.zip": _frames_part0_files,
        "frames-001.zip": _frames_part1_files,
    },
    "current_frames_edge": {
        "frames-000.zip": _frames_edge_files,
    },
    "current_empty_frames": {
        "frames-000.zip": dict,
    },
    "current_memories_no_files": {
        "memories-000.zip": _current_memories_without_files,
    },
}


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
    "synthetic_export_login_history.zip": _legacy_with_login_history_files,
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


def build_all(target_dir: Path = FIXTURES_DIR) -> list[Path]:
    """Write every fixture under ``target_dir`` (default: this directory).

    The regeneration-guard test builds into a temporary directory and
    byte-compares against the checked-in binaries.
    """
    written: list[Path] = []
    for filename, builder in FIXTURES.items():
        out = target_dir / filename
        _write_zip(out, builder())
        written.append(out)
    for filename, raw in RAW_FIXTURES.items():
        out = target_dir / filename
        out.write_bytes(raw)
        written.append(out)
    for set_name, outputs in FIXTURE_SETS.items():
        set_dir = target_dir / set_name
        set_dir.mkdir(exist_ok=True)
        for filename, builder in outputs.items():
            out = set_dir / filename
            result = builder()
            if isinstance(result, dict):
                _write_zip(out, result)
            else:
                out.write_bytes(result)
            written.append(out)
    return written


def main() -> None:
    written = build_all()
    print(f"Wrote {len(written)} fixtures to {FIXTURES_DIR}:")
    for p in written:
        size = p.stat().st_size
        name = p.relative_to(FIXTURES_DIR)
        print(f"  {str(name):<45} {size:>6} bytes")


if __name__ == "__main__":
    main()
