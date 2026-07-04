"""Read a Claude.ai data-export ZIP and produce a typed in-memory model.

See ``docs/export-schema.md`` for the source-format reference. The parser is
defensive about missing optional fields, unknown extras, and structural quirks
(orphan parents, parent cycles, dangling project memories, account-uuid
mismatches) — see ``tests/fixtures/build_fixtures.py`` for the full set of
behaviours covered.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


SENTINEL_PARENT_UUID = "00000000-0000-4000-8000-000000000000"
KNOWN_SENDERS = frozenset({"human", "assistant"})
BATCH_FILENAME_RE = re.compile(r"-batch-(\d{4})\.zip$", re.IGNORECASE)


class ExportFormatError(Exception):
    """Raised when an export archive's structure is incompatible with this parser."""


# ---------------------------------------------------------------------------
# Typed records — see plan §3 for the data-model contract.
# ---------------------------------------------------------------------------


# Sender stays as a plain ``str`` rather than a strict Literal so unknown
# values surface in the parsed data instead of crashing the parser.
Sender = str


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    citations: list[Any] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    thinking: str
    summaries: list[Any] = field(default_factory=list)
    truncated: bool = False
    cut_off: bool = False


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    integration_name: str | None = None
    is_mcp: bool = False


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str
    name: str
    is_error: bool
    inner: list[dict[str, Any]]


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True, slots=True)
class Attachment:
    file_name: str
    file_type: str
    file_size: int
    extracted_content: str


@dataclass(frozen=True, slots=True)
class FileRef:
    file_uuid: str
    file_name: str


# Message and Conversation override ``__repr__`` to keep message bodies out of
# stack traces, log lines, and pytest assertion failures. The default frozen
# dataclass repr would dump every nested ``content[*].text`` value.
@dataclass(frozen=True, slots=True, repr=False)
class Message:
    uuid: str
    sender: Sender
    parent_uuid: str | None
    created_at: datetime
    updated_at: datetime
    content: list[ContentBlock]
    attachments: list[Attachment] = field(default_factory=list)
    files: list[FileRef] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"Message(uuid={self.uuid!r}, sender={self.sender!r}, "
            f"content={len(self.content)}, attachments={len(self.attachments)}, "
            f"files={len(self.files)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class Conversation:
    uuid: str
    title: str
    summary: str
    created_at: datetime
    updated_at: datetime
    messages: list[Message]
    children_by_uuid: dict[str, list[str]]
    canonical_path: list[str]
    branch_points: list[str]

    def __repr__(self) -> str:
        return (
            f"Conversation(uuid={self.uuid!r}, title={self.title!r}, "
            f"messages={len(self.messages)}, "
            f"canonical_path={len(self.canonical_path)}, "
            f"branch_points={len(self.branch_points)})"
        )


@dataclass(frozen=True, slots=True)
class ProjectDoc:
    uuid: str
    filename: str
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GlobalMemory:
    content: str
    account_uuid: str


@dataclass(frozen=True, slots=True)
class ProjectMemory:
    content: str
    project_uuid: str
    account_uuid: str


@dataclass(frozen=True, slots=True)
class Project:
    uuid: str
    name: str
    description: str
    is_starter: bool
    is_private: bool
    prompt_template: str
    created_at: datetime
    updated_at: datetime
    creator_name: str
    docs: list[ProjectDoc]
    memory: ProjectMemory | None


@dataclass(frozen=True, slots=True)
class User:
    uuid: str
    full_name: str
    email_address: str


@dataclass(frozen=True, slots=True)
class Export:
    user: User
    global_memory: GlobalMemory
    projects: list[Project]
    conversations: list[Conversation]
    orphan_project_memories: list[ProjectMemory]
    source_paths: list[str]


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def load_export(source: str | Path | Sequence[str | Path]) -> Export:
    """Read one or more Claude.ai export ZIPs and return a typed ``Export``.

    ``source`` may be:

    - a path to a single ``.zip`` file,
    - a path to a directory containing one or more ``.zip`` files, or
    - a sequence of zip paths (multi-batch case).

    Multi-batch behaviour: ``users.json`` and ``memories.json`` must agree
    across batches; ``conversations`` and ``projects`` are merged with
    first-batch-wins deduplication by uuid (debug-warned on collision).

    Raises :class:`ExportFormatError` for missing required files, malformed
    JSON, or structural invariants that cannot be relaxed (e.g. ``users.json``
    not containing exactly one record).
    """
    paths = _resolve_paths(source)

    if len(paths) == 1:
        match = BATCH_FILENAME_RE.search(paths[0].name)
        if match and match.group(1) != "0000":
            logger.warning(
                "loaded a single archive with batch index %s — earlier batches may be missing",
                match.group(1),
            )

    parsed = [_load_one_zip(p) for p in paths]
    if len(parsed) == 1:
        return parsed[0]
    return _merge_batches(parsed)


def load_export_from_bytes(data: bytes, *, name: str) -> Export:
    """Parse a single export archive held entirely in memory.

    Same validation pipeline as :func:`load_export`, but nothing is read
    from or written to disk — inspect mode (v1.2 Session K) feeds uploaded
    bytes straight in, honouring its no-trace rule. ``name`` is a display
    name only (typically the browser-supplied filename); it becomes the
    single ``source_paths`` entry and feeds the same batch-index warning a
    path-based load gets.
    """
    match = BATCH_FILENAME_RE.search(name)
    if match and match.group(1) != "0000":
        logger.warning(
            "loaded a single archive with batch index %s — earlier batches may be missing",
            match.group(1),
        )
    try:
        zf_ctx = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ExportFormatError(f"not a valid zip archive: {name} ({e})") from e
    return _parse_zip(zf_ctx, source_name=name)


# ---------------------------------------------------------------------------
# Path resolution.
# ---------------------------------------------------------------------------


def _resolve_paths(source: str | Path | Sequence[str | Path]) -> list[Path]:
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_dir():
            zips = sorted(p.glob("*.zip"))
            if not zips:
                raise ExportFormatError(f"no .zip files found in directory: {p}")
            return zips
        return [p]
    paths = [Path(s) for s in source]
    if not paths:
        raise ExportFormatError("no source paths provided")
    return paths


# ---------------------------------------------------------------------------
# Single-archive loading.
# ---------------------------------------------------------------------------


def _load_one_zip(path: Path) -> Export:
    if not path.is_file():
        raise ExportFormatError(f"export archive not found: {path}")

    try:
        zf_ctx = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ExportFormatError(f"not a valid zip archive: {path} ({e})") from e

    return _parse_zip(zf_ctx, source_name=str(path))


def _parse_zip(zf_ctx: zipfile.ZipFile, *, source_name: str) -> Export:
    """Shared parse body for path-based and in-memory loads. ``source_name``
    is what lands in ``Export.source_paths`` — a filesystem path for
    :func:`load_export`, a bare filename for :func:`load_export_from_bytes`."""
    with zf_ctx as zf:
        names = set(zf.namelist())
        for required in ("users.json", "memories.json", "conversations.json"):
            if required not in names:
                raise ExportFormatError(f"missing required file: {required}")

        user = _load_users(zf)
        global_memory, project_memories_by_uuid = _load_memories(zf)

        if global_memory.account_uuid and global_memory.account_uuid != user.uuid:
            logger.warning(
                "account uuid mismatch: users.json=%s, memories.json=%s",
                user.uuid,
                global_memory.account_uuid,
            )

        projects, orphan_memories = _load_projects(zf, project_memories_by_uuid)
        conversations = _load_conversations(zf, user.uuid)

    return Export(
        user=user,
        global_memory=global_memory,
        projects=projects,
        conversations=conversations,
        orphan_project_memories=orphan_memories,
        source_paths=[source_name],
    )


# ---------------------------------------------------------------------------
# users.json
# ---------------------------------------------------------------------------


def _load_users(zf: zipfile.ZipFile) -> User:
    raw = _read_json(zf, "users.json")
    if not isinstance(raw, list):
        raise ExportFormatError("users.json must be a list")
    if len(raw) != 1:
        raise ExportFormatError(f"users.json must contain exactly one record, got {len(raw)}")
    rec = raw[0]
    if not isinstance(rec, dict) or "uuid" not in rec:
        raise ExportFormatError("users.json record missing required field: uuid")
    return User(
        uuid=rec["uuid"],
        full_name=rec.get("full_name", "") or "",
        email_address=rec.get("email_address", "") or "",
    )


# ---------------------------------------------------------------------------
# memories.json
# ---------------------------------------------------------------------------


def _load_memories(
    zf: zipfile.ZipFile,
) -> tuple[GlobalMemory, dict[str, ProjectMemory]]:
    raw = _read_json(zf, "memories.json")
    if not isinstance(raw, list) or len(raw) != 1:
        raise ExportFormatError("memories.json must contain exactly one record")
    rec = raw[0]
    if not isinstance(rec, dict):
        raise ExportFormatError("memories.json record must be a dict")
    account_uuid = rec.get("account_uuid", "") or ""
    global_memory = GlobalMemory(
        content=rec.get("conversations_memory", "") or "",
        account_uuid=account_uuid,
    )
    project_memories: dict[str, ProjectMemory] = {}
    for puid, body in (rec.get("project_memories") or {}).items():
        project_memories[puid] = ProjectMemory(
            content=body or "",
            project_uuid=puid,
            account_uuid=account_uuid,
        )
    return global_memory, project_memories


# ---------------------------------------------------------------------------
# projects/<uuid>.json
# ---------------------------------------------------------------------------


def _load_projects(
    zf: zipfile.ZipFile,
    project_memories: dict[str, ProjectMemory],
) -> tuple[list[Project], list[ProjectMemory]]:
    project_files = sorted(
        n for n in zf.namelist() if n.startswith("projects/") and n.endswith(".json")
    )

    projects: list[Project] = []
    seen_uuids: set[str] = set()

    for name in project_files:
        raw = _read_json(zf, name)
        if not isinstance(raw, dict):
            logger.warning("skipping malformed project file: %s", name)
            continue
        puid = raw.get("uuid")
        if not puid:
            logger.warning("project file %s missing uuid; skipping", name)
            continue
        seen_uuids.add(puid)
        creator = raw.get("creator") or {}
        projects.append(
            Project(
                uuid=puid,
                name=raw.get("name", "") or "",
                description=raw.get("description", "") or "",
                is_starter=bool(raw.get("is_starter_project")),
                is_private=bool(raw.get("is_private")),
                prompt_template=raw.get("prompt_template", "") or "",
                created_at=_parse_dt(raw.get("created_at")),
                updated_at=_parse_dt(raw.get("updated_at")),
                creator_name=creator.get("full_name", "") or "",
                docs=[_parse_doc(d) for d in raw.get("docs") or []],
                memory=project_memories.get(puid),
            )
        )

    orphan_memories: list[ProjectMemory] = []
    for puid, mem in project_memories.items():
        if puid not in seen_uuids:
            logger.warning("dangling project memory for uuid not in archive: %s", puid)
            orphan_memories.append(mem)

    return projects, orphan_memories


def _parse_doc(raw: dict[str, Any]) -> ProjectDoc:
    return ProjectDoc(
        uuid=raw.get("uuid", "") or "",
        filename=raw.get("filename", "") or "",
        content=raw.get("content", "") or "",
        created_at=_parse_dt(raw.get("created_at")),
    )


# ---------------------------------------------------------------------------
# conversations.json
# ---------------------------------------------------------------------------


def _load_conversations(zf: zipfile.ZipFile, account_uuid: str) -> list[Conversation]:
    raw = _read_json(zf, "conversations.json")
    if not isinstance(raw, list):
        raise ExportFormatError("conversations.json must be a list")

    seen_warnings: set[str] = set()
    conversations: list[Conversation] = []

    for rec in raw:
        if not isinstance(rec, dict):
            logger.warning("skipping non-dict conversation entry")
            continue
        cuid = rec.get("uuid")
        if not cuid:
            raise ExportFormatError("conversation missing required field: uuid")
        if "chat_messages" not in rec:
            raise ExportFormatError(f"conversation {cuid} missing required field: chat_messages")

        rec_account = (rec.get("account") or {}).get("uuid")
        if (
            rec_account
            and account_uuid
            and rec_account != account_uuid
            and "account_mismatch" not in seen_warnings
        ):
            logger.warning(
                "account uuid mismatch on conversation %s: %s vs %s",
                cuid,
                rec_account,
                account_uuid,
            )
            seen_warnings.add("account_mismatch")

        messages = [_parse_message(m, seen_warnings) for m in rec.get("chat_messages") or []]
        children, canonical, branch_points = _build_tree(messages)

        conversations.append(
            Conversation(
                uuid=cuid,
                title=rec.get("name", "") or "",
                summary=rec.get("summary", "") or "",
                created_at=_parse_dt(rec.get("created_at")),
                updated_at=_parse_dt(rec.get("updated_at")),
                messages=messages,
                children_by_uuid=children,
                canonical_path=canonical,
                branch_points=branch_points,
            )
        )

    return conversations


# ---------------------------------------------------------------------------
# Message and content-block parsing.
# ---------------------------------------------------------------------------


def _parse_message(raw: dict[str, Any], seen_warnings: set[str]) -> Message:
    sender = raw.get("sender", "") or ""
    if sender not in KNOWN_SENDERS and f"sender:{sender}" not in seen_warnings:
        logger.warning("unknown sender value: %r", sender)
        seen_warnings.add(f"sender:{sender}")

    parsed_content: list[ContentBlock] = []
    for blk in raw.get("content") or []:
        block = _parse_content_block(blk, seen_warnings)
        if block is not None:
            parsed_content.append(block)

    parent_norm = _normalise_parent(raw.get("parent_message_uuid"))

    # ``parent_message_uuid`` absent from the dict entirely is also a root form.
    if "parent_message_uuid" not in raw:
        parent_norm = None

    return Message(
        uuid=raw["uuid"],
        sender=sender,
        parent_uuid=parent_norm,
        created_at=_parse_dt(raw.get("created_at")),
        updated_at=_parse_dt(raw.get("updated_at")),
        content=parsed_content,
        attachments=[_parse_attachment(a) for a in raw.get("attachments") or []],
        files=[_parse_file(f) for f in raw.get("files") or []],
    )


def _normalise_parent(value: Any) -> str | None:
    if value is None or value == "" or value == SENTINEL_PARENT_UUID:
        return None
    return str(value)


def _parse_content_block(raw: dict[str, Any], seen_warnings: set[str]) -> ContentBlock | None:
    block_type = raw.get("type")

    if block_type == "text":
        return TextBlock(
            text=raw.get("text", "") or "",
            citations=raw.get("citations") or [],
        )
    if block_type == "thinking":
        return ThinkingBlock(
            thinking=raw.get("thinking", "") or "",
            summaries=raw.get("summaries") or [],
            truncated=bool(raw.get("truncated")),
            cut_off=bool(raw.get("cut_off")),
        )
    if block_type == "tool_use":
        name = raw.get("name", "") or ""
        return ToolUseBlock(
            id=raw.get("id", "") or "",
            name=name,
            input=raw.get("input") or {},
            integration_name=raw.get("integration_name"),
            is_mcp=bool(raw.get("is_mcp_app")) or ":" in name,
        )
    if block_type == "tool_result":
        return ToolResultBlock(
            tool_use_id=raw.get("tool_use_id", "") or "",
            name=raw.get("name", "") or "",
            is_error=bool(raw.get("is_error")),
            inner=raw.get("content") or [],
        )

    key = f"unknown_block:{block_type}"
    if key not in seen_warnings:
        logger.warning("unknown content block type %r — dropped", block_type)
        seen_warnings.add(key)
    return None


def _parse_attachment(raw: dict[str, Any]) -> Attachment:
    return Attachment(
        file_name=raw.get("file_name", "") or "",
        file_type=raw.get("file_type", "") or "",
        file_size=int(raw.get("file_size") or 0),
        extracted_content=raw.get("extracted_content", "") or "",
    )


def _parse_file(raw: dict[str, Any]) -> FileRef:
    return FileRef(
        file_uuid=raw.get("file_uuid", "") or "",
        file_name=raw.get("file_name", "") or "",
    )


# ---------------------------------------------------------------------------
# Parent-message tree: cycle-tolerant canonical path computation.
# ---------------------------------------------------------------------------


def _build_tree(
    messages: list[Message],
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Compute the parent→children map, the canonical render path, and branch points.

    A "root" is any message whose ``parent_uuid`` is ``None`` (sentinel/null/empty
    or omitted) **or** whose parent uuid points to a uuid not present in this
    conversation (orphan parent). The latter case is logged.

    Walks down from roots marking visited; messages not visited are unreachable
    (a self-loop, a multi-node cycle, or an isolated cluster), logged, and
    excluded from ``canonical_path``.

    The canonical path is the chain ending at the visited leaf with the latest
    ``created_at``. ``branch_points`` are visited parents with more than one
    visited child — these are the spots a UI can offer a sibling switcher.
    """
    msg_by_uuid = {m.uuid: m for m in messages}
    children: dict[str, list[str]] = {m.uuid: [] for m in messages}
    roots: list[str] = []

    for m in messages:
        if m.parent_uuid is None:
            roots.append(m.uuid)
        elif m.parent_uuid not in msg_by_uuid:
            logger.warning(
                "orphan parent_message_uuid %s on message %s — treating as root",
                m.parent_uuid,
                m.uuid,
            )
            roots.append(m.uuid)
        else:
            children[m.parent_uuid].append(m.uuid)

    if len(roots) > 1:
        logger.warning(
            "conversation has %d root messages (expected 1) — using union of trees",
            len(roots),
        )

    visited: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        for child in children.get(node, []):
            if child not in visited:
                stack.append(child)

    for m in messages:
        if m.uuid not in visited:
            logger.warning(
                "message %s is unreachable from any root (likely in a cycle) — "
                "excluded from canonical_path",
                m.uuid,
            )

    if not visited:
        return children, [], []

    leaves = [msg_by_uuid[u] for u in visited if not any(c in visited for c in children.get(u, []))]
    if not leaves:
        return children, [], []

    # Tiebreak by uuid so equal timestamps produce a deterministic canonical path.
    leaf = max(leaves, key=lambda m: (m.created_at, m.uuid))

    canonical: list[str] = []
    walked: set[str] = set()
    cur: str | None = leaf.uuid
    while cur is not None and cur in msg_by_uuid:
        if cur in walked:
            break  # safety net; shouldn't trigger because cycles are excluded above
        walked.add(cur)
        canonical.append(cur)
        parent = msg_by_uuid[cur].parent_uuid
        if parent is None or parent not in msg_by_uuid:
            break
        cur = parent
    canonical.reverse()

    branch_points = [u for u in visited if sum(1 for c in children.get(u, []) if c in visited) > 1]

    return children, canonical, branch_points


# ---------------------------------------------------------------------------
# Multi-batch merge.
# ---------------------------------------------------------------------------


def _merge_batches(batches: list[Export]) -> Export:
    head = batches[0]
    user = head.user
    global_memory = head.global_memory

    for b in batches[1:]:
        if b.user != user:
            raise ExportFormatError(
                "users.json differs across batches: " f"{user.uuid} vs {b.user.uuid}"
            )
        if b.global_memory != global_memory:
            raise ExportFormatError("memories.json (global memory) differs across batches")

    seen_conv: set[str] = set()
    conversations: list[Conversation] = []
    for b in batches:
        for c in b.conversations:
            if c.uuid in seen_conv:
                logger.debug("duplicate conversation uuid across batches: %s", c.uuid)
                continue
            seen_conv.add(c.uuid)
            conversations.append(c)

    seen_proj: set[str] = set()
    projects: list[Project] = []
    for b in batches:
        for p in b.projects:
            if p.uuid in seen_proj:
                logger.debug("duplicate project uuid across batches: %s", p.uuid)
                continue
            seen_proj.add(p.uuid)
            projects.append(p)

    seen_orphan: set[str] = set()
    orphans: list[ProjectMemory] = []
    for b in batches:
        for om in b.orphan_project_memories:
            if om.project_uuid in seen_orphan:
                continue
            seen_orphan.add(om.project_uuid)
            orphans.append(om)

    return Export(
        user=user,
        global_memory=global_memory,
        projects=projects,
        conversations=conversations,
        orphan_project_memories=orphans,
        source_paths=[p for b in batches for p in b.source_paths],
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _read_json(zf: zipfile.ZipFile, name: str) -> Any:
    try:
        with zf.open(name) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ExportFormatError(f"invalid JSON in {name}: {e}") from e


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO 8601 timestamp from the export to a UTC-aware datetime.

    The export uses two forms — ``Z`` suffix (conversations/projects) and
    ``+00:00`` offset (messages). ``datetime.fromisoformat`` handles both on
    Python 3.11+; we also tolerate naive timestamps by attaching UTC.
    Falls back to the Unix epoch if the value is missing or malformed.
    """
    if not value:
        return datetime.fromtimestamp(0, tz=UTC)
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("could not parse timestamp %r — defaulting to epoch", value)
        return datetime.fromtimestamp(0, tz=UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# CLI for quick smoke-testing against a real archive.
# ---------------------------------------------------------------------------


def _main() -> None:
    import sys

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    if len(sys.argv) != 2:
        print("Usage: python -m viewer.parser <export.zip|dir>", file=sys.stderr)
        sys.exit(2)

    export = load_export(sys.argv[1])
    total_msgs = sum(len(c.messages) for c in export.conversations)
    branched = sum(1 for c in export.conversations if c.branch_points)
    project_memories = sum(1 for p in export.projects if p.memory is not None)

    print(f"Sources       : {', '.join(export.source_paths)}")
    print(f"User          : {export.user.full_name} <{export.user.email_address}>")
    print(f"Conversations : {len(export.conversations)}")
    print(f"Messages      : {total_msgs}")
    print(f"Branched convs: {branched}")
    print(f"Projects      : {len(export.projects)}")
    print(f"Proj memories : {project_memories}")
    print(f"Orphan mems   : {len(export.orphan_project_memories)}")


if __name__ == "__main__":
    _main()
