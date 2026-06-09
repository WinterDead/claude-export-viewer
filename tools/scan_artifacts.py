"""Scan a Claude.ai export ZIP and report aggregate statistics about artifact tool calls."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

# Mirrors viewer.parser.SENTINEL_PARENT_UUID — duplicated so the tool stays stdlib-only
# and runnable against any export without the project installed.
SENTINEL_PARENT_UUID = "00000000-0000-4000-8000-000000000000"

ARTIFACT_TOOL_NAME = "artifacts"

# Input fields whose values are content-bearing: only their lengths are recorded.
# Everything printed by this tool is an aggregate — no content ever appears in output.
CONTENT_FIELDS = ("content", "old_str", "new_str", "title")

# Literal markers whose presence in content would indicate explicit truncation.
TRUNCATION_MARKERS = (
    "[truncated",
    "[content truncated",
    "(truncated)",
    "<truncated",
    "...remainder omitted",
)

# A repeated tool_result text value is treated as a system status string (safe to print)
# only if it is short and recurs across many distinct conversations.
STATUS_MAX_LEN = 60
STATUS_MIN_CONVERSATIONS = 5


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalise_parent(value: Any) -> str | None:
    if value is None or value == "" or value == SENTINEL_PARENT_UUID:
        return None
    return str(value)


def canonical_path(messages: list[dict[str, Any]]) -> tuple[list[str], set[str]]:
    """Return (canonical message-uuid path, visited uuids), mirroring viewer.parser._build_tree.

    Roots are messages with a sentinel/null/missing parent or a parent uuid not present in
    the conversation. The canonical path is the chain ending at the visited leaf with the
    latest ``created_at`` (uuid tiebreak). Unreachable messages (cycles) are excluded.
    """
    msg_by_uuid = {m["uuid"]: m for m in messages}
    children: dict[str, list[str]] = {m["uuid"]: [] for m in messages}
    roots: list[str] = []

    for m in messages:
        parent = _normalise_parent(m.get("parent_message_uuid"))
        if parent is None or parent not in msg_by_uuid:
            roots.append(m["uuid"])
        else:
            children[parent].append(m["uuid"])

    visited: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(c for c in children.get(node, []) if c not in visited)

    if not visited:
        return [], set()

    leaves = [msg_by_uuid[u] for u in visited if not any(c in visited for c in children.get(u, []))]
    if not leaves:
        return [], visited

    def leaf_key(m: dict[str, Any]) -> tuple[datetime | str, str]:
        ts = _parse_ts(m.get("created_at"))
        return (ts if ts is not None else "", m["uuid"])

    # max() needs comparable keys; mixed datetime/str only occurs on malformed timestamps.
    dated = [m for m in leaves if _parse_ts(m.get("created_at")) is not None]
    pool = dated if dated else leaves
    leaf = max(pool, key=leaf_key)

    path: list[str] = []
    walked: set[str] = set()
    cur: str | None = leaf["uuid"]
    while cur is not None and cur in msg_by_uuid:
        if cur in walked:
            break
        walked.add(cur)
        path.append(cur)
        cur = _normalise_parent(msg_by_uuid[cur].get("parent_message_uuid"))
    path.reverse()
    return path, visited


def _fence_balanced(content: str) -> bool:
    return content.count("```") % 2 == 0


def _content_checks(artifact_type: str, content: str) -> dict[str, bool]:
    """Per-type well-formedness heuristics. True means the check passed."""
    checks: dict[str, bool] = {"fence_balanced": _fence_balanced(content)}
    stripped = content.rstrip()
    if artifact_type == "text/html":
        checks["closes_html"] = stripped.endswith("</html>")
    elif artifact_type == "image/svg+xml":
        checks["closes_svg"] = stripped.endswith("</svg>")
    elif "json" in artifact_type:
        try:
            json.loads(content)
            checks["json_parses"] = True
        except ValueError:
            checks["json_parses"] = False
    return checks


def collect_calls(
    conversations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Walk every message in every conversation (all branches) and record artifact calls.

    Returns (per-call records, number of conversations with at least one branch point).
    """
    calls: list[dict[str, Any]] = []
    branched_convs = 0
    for conv in conversations:
        messages = conv.get("chat_messages", [])
        path, visited = canonical_path(messages)
        path_index = {uuid: i for i, uuid in enumerate(path)}
        if len(visited) > len(path):
            branched_convs += 1

        # Map tool_use id → tool_result location for pairing (when ids are non-null).
        results: dict[str, dict[str, Any]] = {}
        for m_idx, msg in enumerate(messages):
            for b_idx, block in enumerate(msg.get("content") or []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tu_id = block.get("tool_use_id")
                    if isinstance(tu_id, str) and tu_id not in results:
                        results[tu_id] = {"msg_idx": m_idx, "block_idx": b_idx, "block": block}

        for m_idx, msg in enumerate(messages):
            for b_idx, block in enumerate(msg.get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != ARTIFACT_TOOL_NAME:
                    continue
                raw_input = block.get("input")
                inp = raw_input if isinstance(raw_input, dict) else {}
                lengths = {f: len(inp[f]) for f in CONTENT_FIELDS if isinstance(inp.get(f), str)}
                content = inp.get("content")
                artifact_type = inp.get("type")
                checks: dict[str, bool] = {}
                markers: list[str] = []
                if isinstance(content, str) and content:
                    checks = _content_checks(
                        artifact_type if isinstance(artifact_type, str) else "", content
                    )
                    markers = [mk for mk in TRUNCATION_MARKERS if mk in content]

                # Pair by id when present; observed exports carry null ids on artifact
                # blocks, so fall back to positional adjacency: the tool_result that
                # directly follows the tool_use in the same message, with the same name.
                pair = None
                rinfo: dict[str, Any] | None = None
                tu_id = block.get("id")
                if isinstance(tu_id, str) and tu_id in results:
                    rinfo = results[tu_id]
                else:
                    blocks = msg.get("content") or []
                    if b_idx + 1 < len(blocks):
                        nxt = blocks[b_idx + 1]
                        if (
                            isinstance(nxt, dict)
                            and nxt.get("type") == "tool_result"
                            and nxt.get("name") == block.get("name")
                        ):
                            rinfo = {"msg_idx": m_idx, "block_idx": b_idx + 1, "block": nxt}
                if rinfo is not None:
                    rblock = rinfo["block"]
                    inner = rblock.get("content") or []
                    inner_types = [b.get("type") for b in inner if isinstance(b, dict)]
                    texts = [
                        b.get("text")
                        for b in inner
                        if isinstance(b, dict) and isinstance(b.get("text"), str)
                    ]
                    pair = {
                        "same_message": rinfo["msg_idx"] == m_idx,
                        "positional": not (isinstance(tu_id, str) and tu_id in results),
                        "is_error": rblock.get("is_error"),
                        "name": rblock.get("name"),
                        "inner_types": inner_types,
                        "texts": texts,
                    }

                calls.append(
                    {
                        "conv_uuid": conv.get("uuid"),
                        "msg_uuid": msg["uuid"],
                        "msg_idx": m_idx,
                        "block_idx": b_idx,
                        "sender": msg.get("sender"),
                        "created_at": msg.get("created_at"),
                        "on_canonical": msg["uuid"] in path_index,
                        "canonical_idx": path_index.get(msg["uuid"]),
                        "reachable": msg["uuid"] in visited,
                        "command": inp.get("command"),
                        "input_fields": sorted(inp.keys()),
                        "field_shapes": {k: _value_shape(v) for k, v in inp.items()},
                        "lengths": lengths,
                        "artifact_id": inp.get("id"),
                        "artifact_type": artifact_type,
                        "language": inp.get("language"),
                        "version_uuid": inp.get("version_uuid"),
                        "checks": checks,
                        "markers": markers,
                        "pair": pair,
                    }
                )
    return calls, branched_convs


def _summarise_lengths(values: list[int]) -> dict[str, int]:
    if not values:
        return {"n": 0}
    vs = sorted(values)
    n = len(vs)
    return {
        "n": n,
        "min": vs[0],
        "p50": vs[n // 2],
        "p90": vs[int(0.9 * (n - 1))],
        "max": vs[-1],
    }


def _value_shape(value: Any) -> str:
    """Classify a field value by shape, never by content."""
    if value is None:
        return "null"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "empty_list" if not value else "list"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def aggregate(
    conversations: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    branched_convs: int,
) -> dict[str, Any]:
    """Reduce per-call records to the aggregate report structure."""
    by_command = Counter(str(c["command"]) for c in calls)
    by_type = Counter(str(c["artifact_type"]) for c in calls)
    by_language = Counter(
        str(c["language"]) for c in calls if c["command"] in ("create", "rewrite")
    )

    field_presence: dict[str, Counter[str]] = defaultdict(Counter)
    field_shapes: dict[str, Counter[str]] = defaultdict(Counter)
    for c in calls:
        for f in c["input_fields"]:
            field_presence[str(c["command"])][f] += 1
        for f, shape in c["field_shapes"].items():
            field_shapes[f"{c['command']}.{f}"][shape] += 1

    lengths_by_cmd: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for c in calls:
        for f, ln in c["lengths"].items():
            lengths_by_cmd[str(c["command"])][f].append(ln)

    # Histories: one artifact = (conversation uuid, artifact id).
    histories: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in calls:
        if isinstance(c["artifact_id"], str) and isinstance(c["conv_uuid"], str):
            histories[(c["conv_uuid"], c["artifact_id"])].append(c)

    history_sig = Counter()
    branch_class = Counter()
    multi_create = 0
    for hist in histories.values():
        hist.sort(key=lambda c: (str(c["created_at"]), c["msg_idx"], c["block_idx"]))
        history_sig[",".join(str(c["command"]) for c in hist)] += 1
        on = [c["on_canonical"] for c in hist]
        branch_class[
            "all_canonical" if all(on) else "all_off_canonical" if not any(on) else "mixed"
        ] += 1
        if sum(1 for c in hist if c["command"] == "create") > 1:
            multi_create += 1

    per_conv = Counter()
    for conv_uuid, _ in histories:
        per_conv[conv_uuid] += 1
    artifacts_per_conv = Counter(per_conv.values())

    pairing = Counter()
    pair_errors = Counter()
    inner_types = Counter()
    status_by_value: dict[str, set[str]] = defaultdict(set)
    status_counts = Counter()
    for c in calls:
        pair = c["pair"]
        if pair is None:
            pairing["no_result"] += 1
            continue
        loc = "same_message" if pair["same_message"] else "other_message"
        method = "positional" if pair["positional"] else "by_id"
        pairing[f"{loc}/{method}"] += 1
        pair_errors[str(pair["is_error"])] += 1
        inner_types.update(str(t) for t in pair["inner_types"])
        for text in pair["texts"]:
            if len(text) <= STATUS_MAX_LEN:
                status_by_value[text].add(str(c["conv_uuid"]))
                status_counts[text] += 1

    status_strings = {
        repr(v): status_counts[v]
        for v, convs in status_by_value.items()
        if len(convs) >= STATUS_MIN_CONVERSATIONS
    }

    check_totals: dict[str, Counter[str]] = defaultdict(Counter)
    for c in calls:
        for name, ok in c["checks"].items():
            key = f"{c['artifact_type']}:{name}"
            check_totals[key]["pass" if ok else "fail"] += 1

    marker_hits = Counter()
    for c in calls:
        for mk in c["markers"]:
            marker_hits[mk] += 1

    content_lengths = [
        c["lengths"]["content"]
        for c in calls
        if c["command"] in ("create", "rewrite") and "content" in c["lengths"]
    ]
    length_pileups = Counter(content_lengths).most_common(5)

    version_uuids = [c["version_uuid"] for c in calls if isinstance(c["version_uuid"], str)]

    convs_with_calls = {c["conv_uuid"] for c in calls}

    return {
        "export": {
            "conversations": len(conversations),
            "messages": sum(len(c.get("chat_messages", [])) for c in conversations),
            "conversations_with_branches": branched_convs,
            "conversations_with_artifact_calls": len(convs_with_calls),
        },
        "calls": {
            "total": len(calls),
            "by_command": dict(by_command),
            "by_artifact_type": dict(by_type),
            "by_language_create_rewrite": dict(by_language),
            "senders": dict(Counter(str(c["sender"]) for c in calls)),
            "unreachable": sum(1 for c in calls if not c["reachable"]),
        },
        "input_fields_by_command": {k: dict(v) for k, v in field_presence.items()},
        "input_field_value_shapes": {k: dict(v) for k, v in sorted(field_shapes.items())},
        "length_summaries": {
            cmd: {f: _summarise_lengths(v) for f, v in fields.items()}
            for cmd, fields in lengths_by_cmd.items()
        },
        "histories": {
            "distinct_artifacts": len(histories),
            "multi_command": sum(1 for h in histories.values() if len(h) > 1),
            "multi_create_ids": multi_create,
            "command_signatures": dict(history_sig.most_common(20)),
            "branch_class": dict(branch_class),
        },
        "artifacts_per_conversation": dict(sorted(artifacts_per_conv.items())),
        "tool_result_pairing": {
            "location": dict(pairing),
            "is_error": dict(pair_errors),
            "inner_types": dict(inner_types),
            "recurrent_status_strings": status_strings,
        },
        "truncation": {
            "content_length_pileups_top5": length_pileups,
            "well_formedness_checks": {k: dict(v) for k, v in check_totals.items()},
            "explicit_markers": dict(marker_hits),
        },
        "version_uuid": {
            "present": len(version_uuids),
            "distinct": len(set(version_uuids)),
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
            "Inventory artifact tool calls in a Claude.ai export ZIP. "
            "Output is aggregate-only: no conversation content is printed."
        )
    )
    parser.add_argument("zip_path", help="path to the export ZIP")
    parser.add_argument("--json", action="store_true", help="emit the aggregates as JSON")
    args = parser.parse_args(argv)

    with zipfile.ZipFile(args.zip_path) as zf:
        with zf.open("conversations.json") as fh:
            conversations = json.load(fh)

    calls, branched_convs = collect_calls(conversations)
    agg = aggregate(conversations, calls, branched_convs)

    if args.json:
        print(json.dumps(agg, indent=2))
    else:
        print(format_report(agg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
