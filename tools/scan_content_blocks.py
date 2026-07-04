"""Scan a Claude.ai export ZIP and report aggregate statistics about message content blocks.

Companion to scan_artifacts.py (same pattern: stdlib-only, aggregate-only). Where that tool
inventories artifact calls in depth, this one takes a census of everything: every content-block
type, sender value, tool name, thinking flag, attachment, and file reference across all branches
of every conversation. Its purpose is to check the rendering table in docs/export-design.md
against what actually occurs in an export — any block type the table does not cover is listed
prominently so it gets a rendering decision before implementation.

Output is aggregate-only: counts, type names, tool names, and size buckets. No conversation
content, titles, or identifiers are ever printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter, defaultdict
from typing import Any

ARTIFACT_TOOL_NAME = "artifacts"

# The content-block types the rendering table in docs/export-design.md explicitly covers.
# Anything else observed in an export is reported as unmapped: a rendering decision is
# required before the serializer is built. (Attachments and files are message-level fields,
# not content blocks; they have their own table rows and their own sections below.)
DESIGN_TABLE_BLOCK_TYPES = frozenset({"text", "thinking", "tool_use", "tool_result"})

# Attachment extracted-text size buckets (UTF-8 bytes), coarse by design: they size the
# attachment-toggle decision, not a storage budget.
SIZE_BUCKETS: tuple[tuple[str, int], ...] = (
    ("<1 KB", 1_024),
    ("1-10 KB", 10_240),
    ("10-100 KB", 102_400),
)
SIZE_BUCKET_TOP = ">100 KB"


def _block_type(block: Any) -> str:
    """Name a block's type without touching its content; non-dict blocks get a shape label."""
    if not isinstance(block, dict):
        return f"<non-dict:{type(block).__name__}>"
    return str(block.get("type"))


def _size_bucket(n_bytes: int) -> str:
    for label, upper in SIZE_BUCKETS:
        if n_bytes < upper:
            return label
    return SIZE_BUCKET_TOP


def census(conversations: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk every message in every conversation (all branches) and reduce to aggregates."""
    n_messages = 0
    n_blocks = 0

    block_counts: Counter[str] = Counter()
    block_convs: dict[str, set[str]] = defaultdict(set)
    senders: Counter[str] = Counter()

    thinking_total = 0
    thinking_truncated = 0
    thinking_cut_off = 0

    tool_use_names: Counter[str] = Counter()
    tool_result_names: Counter[str] = Counter()
    tool_result_is_error: Counter[str] = Counter()
    tool_result_inner_types: Counter[str] = Counter()

    attachments_total = 0
    attachment_msgs = 0
    attachment_sizes: Counter[str] = Counter()
    attachments_no_text = 0

    files_total = 0
    file_msgs = 0

    artifact_commands: Counter[str] = Counter()
    artifact_convs: set[str] = set()

    for conv in conversations:
        conv_uuid = str(conv.get("uuid"))
        for msg in conv.get("chat_messages", []):
            n_messages += 1
            senders[str(msg.get("sender"))] += 1

            attachments = msg.get("attachments") or []
            if attachments:
                attachment_msgs += 1
            for att in attachments:
                attachments_total += 1
                extracted = att.get("extracted_content") if isinstance(att, dict) else None
                if isinstance(extracted, str) and extracted:
                    attachment_sizes[_size_bucket(len(extracted.encode("utf-8")))] += 1
                else:
                    attachments_no_text += 1

            files = msg.get("files") or []
            if files:
                file_msgs += 1
            files_total += len(files)

            for block in msg.get("content") or []:
                n_blocks += 1
                btype = _block_type(block)
                block_counts[btype] += 1
                block_convs[btype].add(conv_uuid)
                if not isinstance(block, dict):
                    continue

                if btype == "thinking":
                    thinking_total += 1
                    if block.get("truncated"):
                        thinking_truncated += 1
                    if block.get("cut_off"):
                        thinking_cut_off += 1
                elif btype == "tool_use":
                    name = str(block.get("name"))
                    tool_use_names[name] += 1
                    if name == ARTIFACT_TOOL_NAME:
                        raw_input = block.get("input")
                        inp = raw_input if isinstance(raw_input, dict) else {}
                        artifact_commands[str(inp.get("command"))] += 1
                        artifact_convs.add(conv_uuid)
                elif btype == "tool_result":
                    tool_result_names[str(block.get("name"))] += 1
                    tool_result_is_error[str(block.get("is_error"))] += 1
                    for inner in block.get("content") or []:
                        tool_result_inner_types[_block_type(inner)] += 1

    unmapped = sorted(set(block_counts) - DESIGN_TABLE_BLOCK_TYPES)

    return {
        "export": {
            "conversations": len(conversations),
            "messages": n_messages,
            "content_blocks": n_blocks,
        },
        "block_types": {
            btype: {"blocks": count, "conversations": len(block_convs[btype])}
            for btype, count in block_counts.most_common()
        },
        "unmapped_block_types": unmapped if unmapped else "NONE - design table covers all",
        "senders": dict(senders.most_common()),
        "thinking": {
            "blocks": thinking_total,
            "truncated": thinking_truncated,
            "cut_off": thinking_cut_off,
        },
        "tool_use_names": dict(tool_use_names.most_common()),
        "tool_result": {
            "names": dict(tool_result_names.most_common()),
            "is_error": dict(tool_result_is_error),
            "inner_types": dict(tool_result_inner_types.most_common()),
        },
        "attachments": {
            "total": attachments_total,
            "messages_with_attachments": attachment_msgs,
            "extracted_text_sizes": {
                label: attachment_sizes[label]
                for label in [b[0] for b in SIZE_BUCKETS] + [SIZE_BUCKET_TOP]
                if attachment_sizes[label]
            },
            "empty_or_missing_extracted_text": attachments_no_text,
        },
        "files": {
            "total": files_total,
            "messages_with_files": file_msgs,
        },
        "artifacts": {
            "calls": sum(artifact_commands.values()),
            "by_command": dict(artifact_commands.most_common()),
            "conversations_with_artifact_calls": len(artifact_convs),
        },
    }


def format_report(agg: dict[str, Any]) -> str:
    """Render the aggregate structure as an indented plain-text report."""
    lines: list[str] = []

    def emit(obj: Any, indent: int = 0) -> None:
        pad = "  " * indent
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, dict | list) and v:
                    lines.append(f"{pad}{k}:")
                    emit(v, indent + 1)
                else:
                    lines.append(f"{pad}{k}: {v}")
        elif isinstance(obj, list):
            for item in obj:
                lines.append(f"{pad}- {item}")

    for section, body in agg.items():
        lines.append(f"== {section} ==")
        emit(body, 1)
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Census of message content blocks in a Claude.ai export ZIP. "
            "Output is aggregate-only: no conversation content is printed."
        )
    )
    parser.add_argument("zip_path", help="path to the export ZIP")
    parser.add_argument("--json", action="store_true", help="emit the aggregates as JSON")
    args = parser.parse_args(argv)

    with zipfile.ZipFile(args.zip_path) as zf:
        with zf.open("conversations.json") as fh:
            conversations = json.load(fh)

    agg = census(conversations)

    if args.json:
        print(json.dumps(agg, indent=2))
    else:
        print(format_report(agg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
