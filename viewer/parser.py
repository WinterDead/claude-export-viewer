"""Read a Claude.ai data export and produce a typed in-memory model.

Two container formats load through this module (``docs/export-schema.md`` is
the source-format reference; ``docs/format-support-design.md`` the design
record): the legacy single ZIP, and the current manifest-plus-category-ZIPs
delivery. Detection is by content, never filename (§2 of the record); both
containers normalise into the same :class:`Export`. The parser is defensive
about missing optional fields, unknown extras, and structural quirks (orphan
parents, parent cycles, dangling project memories, account-uuid mismatches) —
see ``tests/fixtures/build_fixtures.py`` for the full set of behaviours
covered.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from viewer.categories import (
    Artifact,
    ArtifactDataFile,
    ScheduledTask,
    TaskRun,
    artifact_ids_in,
    load_artifact_data,
    load_claude_code_cloud,
    load_frames,
)

logger = logging.getLogger(__name__)


SENTINEL_PARENT_UUID = "00000000-0000-4000-8000-000000000000"
KNOWN_SENDERS = frozenset({"human", "assistant"})
BATCH_FILENAME_RE = re.compile(r"-batch-(\d{4})\.zip$", re.IGNORECASE)
# Current-format part naming, e.g. ``conversations-001.zip``. Filenames never
# decide parsing (record §2); this pattern only feeds the isolated-later-part
# warning, the current-format sibling of the legacy batch warning above.
PART_FILENAME_RE = re.compile(r"-(\d{3})\.zip$", re.IGNORECASE)

# Container kinds. ``CONTAINER_LEGACY`` is the single-ZIP format; the four
# category names match the current format's manifest ``category`` values.
CONTAINER_LEGACY = "legacy"
CATEGORY_CONVERSATIONS = "conversations"
CATEGORY_PROJECTS = "projects"
CATEGORY_MEMORIES = "memories"
CATEGORY_LIGHT_METADATA = "light_metadata"
ALL_CATEGORIES = (
    CATEGORY_CONVERSATIONS,
    CATEGORY_PROJECTS,
    CATEGORY_MEMORIES,
    CATEGORY_LIGHT_METADATA,
)
# The four original categories are the **core** set (export-categories record
# §4): without a manifest, the degraded-view banner counts against these.
CORE_CATEGORIES = ALL_CATEGORIES

# The categories exports gained after v1.3.0 (export-categories record §3).
# v1.3.1 recognises, validates, and keeps them; v1.4.0 displays them.
CATEGORY_FRAMES = "frames"
CATEGORY_ARTIFACT_DATA = "artifact_data"
CATEGORY_CLAUDE_CODE_CLOUD = "claude_code_cloud"
LATER_CATEGORIES = (CATEGORY_FRAMES, CATEGORY_ARTIFACT_DATA, CATEGORY_CLAUDE_CODE_CLOUD)

# Every category this version recognises, in banner and summary order.
KNOWN_CATEGORIES = CORE_CATEGORIES + LATER_CATEGORIES

# Content-block types the parser types; anything else is preserved as an
# UnknownBlock. The real-data invariant tests assert no block of these types
# ever reaches UnknownBlock.
KNOWN_BLOCK_TYPES = frozenset(
    {"text", "thinking", "tool_use", "tool_result", "injected_prompt_block", "document"}
)

_LEGACY_TRIO = frozenset({"users.json", "memories.json", "conversations.json"})
_PROJECT_MEMBER_RE = re.compile(r"projects/[^/]+\.json$")
_MEMORY_MEMBER_RE = re.compile(r"memories/[^/]+\.json$")
# Later-category signatures (export-categories record §3).
_ARTIFACT_MEMBER_RE = re.compile(r"artifacts/[^/]+/.+")
_ARTIFACT_DESCRIPTOR_RE = re.compile(r"artifacts/[^/]+/artifact\.json")
_ARTIFACT_DATA_MEMBER_RE = re.compile(r"artifacts/[^/]+/data/[^/]+\.json")
_CLAUDE_CODE_CLOUD_MEMBER_RE = re.compile(r"claude_code_cloud/.+")


class ExportFormatError(Exception):
    """Raised when an export archive's structure is incompatible with this parser."""


class CategoryStructureError(ExportFormatError):
    """A file matched a later category's signature but its structure could
    not be read (export-categories record §5.1). The routes skip the file
    with the D2 notice instead of failing the set."""


class MixedContainerError(ExportFormatError):
    """A submission mixed a legacy archive with current-format category files.

    Refused by design (record §2): the record defines no semantics for the
    mix because no real export produces one. The exception message names the
    offending files for logs; the routes render the Appendix C11 copy
    (:data:`MIXED_CONTAINER_MESSAGE`) instead.
    """


# Appendix C11 — the user-facing form both routes render for a mixed submission.
MIXED_CONTAINER_MESSAGE = (
    "Legacy and current-format export files can't be loaded together — "
    "upload one export's files at a time."
)


# ---------------------------------------------------------------------------
# Typed records — see plan §3 for the data-model contract.
# ---------------------------------------------------------------------------


# Sender stays as a plain ``str`` rather than a strict Literal so unknown
# values surface in the parsed data instead of crashing the parser.
Sender = str


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    # Citation records preserved as given (uuid, offsets into the raw text,
    # details.url) — the Sources-list renderer consumes them (record §9.4).
    citations: list[Any] = field(default_factory=list)
    # Per-block timing, preserved raw and unrendered (record §6).
    start_timestamp: str | None = None
    stop_timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    thinking: str
    # Summary strings, extracted from the export's ``{summary: …}`` items
    # (record §6). The ``signature`` field is deliberately dropped and never
    # reaches this model — the decision of record (§6), disclosed in help.
    summaries: list[str] = field(default_factory=list)
    truncated: bool = False
    cut_off: bool = False
    thinking_hidden: bool = False
    start_timestamp: str | None = None
    stop_timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    integration_name: str | None = None
    is_mcp: bool = False
    # Rendering-hint dict, shape preserved for the record §9 renderers.
    display_content: dict[str, Any] | None = None
    # The product's one-line status for the call ("Searching the web…") — the
    # collapsed display-body teaser (amendment A5, copy U1).
    message: str | None = None
    start_timestamp: str | None = None
    stop_timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str
    name: str
    is_error: bool
    inner: list[dict[str, Any]]
    display_content: dict[str, Any] | None = None
    # ``meta.output_format_category`` — ``md`` routes the result text through
    # the Markdown pipeline (record §9.6).
    output_format_category: str | None = None
    message: str | None = None
    # Preserved raw (v1.4.0): an ``Artifact`` publishing result names the
    # artifact here — the cross-link source of record §6.5.
    structured_content: Any = None
    start_timestamp: str | None = None
    stop_timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class InjectedPromptBlock:
    """Current-format block: prompt text the product injected into the
    conversation, typed per record §6 (previously an UnknownBlock).

    The ``type`` class attribute keeps the pre-typed rendering and export
    fallbacks byte-identical until the record §9.1 card ships: both the
    reading view's placeholder card and the serializer's unrecognised-block
    line name blocks via ``.type``.
    """

    type: ClassVar[str] = "injected_prompt_block"

    prompt: str
    initial_turn_only: bool = False
    skip_on_truncated_continuation: bool = False
    start_timestamp: str | None = None
    stop_timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    """A document the user attached to their turn (export-categories record
    §5.3, V18) — typed since v1.3.1, previously an UnknownBlock. The file
    itself is never in the export; the block names it."""

    file_uuid: str
    # Occasionally absent in real exports; the renderers fall back to D6's
    # "Untitled document".
    title: str | None = None
    flags: Any = None
    start_timestamp: str | None = None
    stop_timestamp: str | None = None


# Preserve-and-flag slot for content-block types this parser doesn't know
# (house rule — see CLAUDE.md). ``type`` is the raw type string from the
# export (empty when the record carried none); ``raw`` is the whole block
# record, untouched, so nothing is lost if a future version learns to render
# it. The repr deliberately omits ``raw`` — same reason Message hides its
# content: unknown blocks may carry conversation text, which must stay out
# of stack traces and log lines.
@dataclass(frozen=True, slots=True, repr=False)
class UnknownBlock:
    type: str
    raw: dict[str, Any]

    def __repr__(self) -> str:
        return f"UnknownBlock(type={self.type!r}, raw_keys={len(self.raw)})"


ContentBlock = (
    TextBlock
    | ThinkingBlock
    | ToolUseBlock
    | ToolResultBlock
    | InjectedPromptBlock
    | DocumentBlock
    | UnknownBlock
)


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
    verified_phone_number: str = ""


# The new memory system's file records (current format only). ``path`` is
# absolute-style with a leading ``/`` and an open-set taxonomy — the viewer
# never enumerates expected directories (record §7).
@dataclass(frozen=True, slots=True)
class MemoryFile:
    path: str
    content: str
    updated_at: datetime


# One login event from ``login_history.json`` (current format only) — the
# Account page's table row (record §8).
@dataclass(frozen=True, slots=True)
class LoginEvent:
    timestamp: datetime
    method: str
    ip_address: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    browser_family: str | None = None
    browser_version: str | None = None
    device_family: str | None = None
    os_family: str | None = None
    os_version: str | None = None


@dataclass(frozen=True, slots=True)
class Export:
    # ``user`` and ``global_memory`` are ``None`` when the current format's
    # light_metadata / memories category is missing from the loaded set — any
    # subset loads, degrading features visibly (record §3). Legacy loads
    # always carry both (the required trio).
    user: User | None
    global_memory: GlobalMemory | None
    projects: list[Project]
    conversations: list[Conversation]
    orphan_project_memories: list[ProjectMemory]
    source_paths: list[str]
    memory_files: list[MemoryFile] = field(default_factory=list)
    login_events: list[LoginEvent] = field(default_factory=list)
    # Which categories the loaded set supplied, in ``KNOWN_CATEGORIES``
    # order — drives the degraded-view banner. A legacy archive carries the
    # four core categories (its login history is empty by format unless the
    # archive carries a root login_history.json, V21).
    categories: tuple[str, ...] = ALL_CATEGORIES
    # The later categories (v1.4.0, export-categories record §6) — metadata only;
    # artifact file bytes are read on demand, never held here.
    artifacts: list[Artifact] = field(default_factory=list)
    artifact_data: list[ArtifactDataFile] = field(default_factory=list)
    scheduled_tasks: list[ScheduledTask] = field(default_factory=list)
    task_runs: list[TaskRun] = field(default_factory=list)
    # Artifact id → uuids of the conversations whose tool blocks name it
    # (record §6.5, provisional), in conversation order.
    artifact_mentions: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def load_export(source: str | Path | Sequence[str | Path]) -> Export:
    """Read a Claude.ai export — either container format — into an ``Export``.

    ``source`` may be:

    - a path to a single ``.zip`` file (a legacy archive or one current
      category zip),
    - a path to a directory of ``.zip`` files (legacy multi-batch, or a
      current-format set of category zips), or
    - a sequence of zip paths.

    Each zip is identified by content (record §2) and the load dispatches:
    current category zips go through the set loader (any subset loads,
    record §3); everything else takes the legacy pipeline, whose required-
    files check raises its precise errors. Mixing the two containers in one
    load is refused — the record defines no semantics for it.

    Legacy multi-batch behaviour is unchanged: ``users.json`` and
    ``memories.json`` must agree across batches; ``conversations`` and
    ``projects`` merge with first-batch-wins deduplication by uuid.

    Raises :class:`ExportFormatError` for missing required files, malformed
    JSON, or structural invariants that cannot be relaxed (e.g. ``users.json``
    not containing exactly one record).
    """
    paths = _resolve_paths(source)

    kinds = {p: _sniff_zip_path(p) for p in paths}
    current = [p for p in paths if kinds[p] in KNOWN_CATEGORIES]
    if current:
        legacy = [p for p in paths if kinds[p] == CONTAINER_LEGACY]
        if legacy:
            raise MixedContainerError(
                "cannot mix current-format category archives with other archives "
                f"in one load: {', '.join(p.name for p in legacy)}"
            )
        # A6: beside category files, an unrecognised ZIP is skipped, not
        # fatal (the routes show D1; a stored set never holds one).
        for p in paths:
            if kinds[p] is None:
                logger.warning("skipped unrecognised file in an export set: %s", p.name)
        parts = []
        for p in current:
            zf, _names = _open_zip(p)
            parts.append((kinds[p], zf, str(p), p.name))
        return _load_current_set(parts)

    # Legacy pipeline — unchanged.
    if len(paths) == 1:
        _warn_if_isolated_batch(paths[0].name)

    parsed = [_load_one_zip(p) for p in paths]
    if len(parsed) == 1:
        return parsed[0]
    return _merge_batches(parsed)


def load_export_from_bytes(data: bytes, *, name: str) -> Export:
    """Parse a single export archive held entirely in memory.

    Same detection and validation pipeline as :func:`load_export`, but
    nothing is read from or written to disk — inspect mode (v1.2 Session K)
    feeds uploaded bytes straight in, honouring its no-trace rule. ``name``
    is a display name only (typically the browser-supplied filename); it
    becomes the single ``source_paths`` entry and feeds the same
    isolated-batch/part warning a path-based load gets.
    """
    _warn_if_isolated_batch(name)
    try:
        zf_ctx = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ExportFormatError(f"not a valid zip archive: {name} ({e})") from e
    kind = sniff_container(set(zf_ctx.namelist()))
    if kind in KNOWN_CATEGORIES:
        return _load_current_set([(kind, zf_ctx, name, name)])
    return _parse_zip(zf_ctx, source_name=name)


def load_export_from_bytes_set(files: Sequence[tuple[str, bytes]]) -> Export:
    """Parse a set of export archives held entirely in memory.

    The multi-file sibling of :func:`load_export_from_bytes` — the inspect
    flow's set input (record §4), honouring the same no-trace rule: nothing
    touches disk. ``files`` holds ``(display_name, zip_bytes)`` pairs in
    supplied order; the same detection dispatch as :func:`load_export`
    applies, mixing refused included.
    """
    if not files:
        raise ExportFormatError("no files supplied")
    parts: list[tuple[str | None, zipfile.ZipFile, str]] = []
    for name, data in files:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ExportFormatError(f"not a valid zip archive: {name} ({e})") from e
        parts.append((sniff_container(set(zf.namelist())), zf, name))

    if any(kind in KNOWN_CATEGORIES for kind, _zf, _name in parts):
        legacy = [name for kind, _zf, name in parts if kind == CONTAINER_LEGACY]
        if legacy:
            raise MixedContainerError(
                "cannot mix current-format category archives with other archives "
                f"in one load: {', '.join(legacy)}"
            )
        for kind, _zf, name in parts:
            if kind is None:
                logger.warning("skipped unrecognised file in an export set: %s", name)
        return _load_current_set(
            [(kind, zf, name, name) for kind, zf, name in parts if kind in KNOWN_CATEGORIES]
        )

    if len(parts) == 1:
        _kind, zf, name = parts[0]
        _warn_if_isolated_batch(name)
        return _parse_zip(zf, source_name=name)
    return _merge_batches([_parse_zip(zf, source_name=name) for _kind, zf, name in parts])


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
# Container detection (record §2) — by content, never filename.
# ---------------------------------------------------------------------------


def sniff_container(names: set[str]) -> str | None:
    """Identify a zip's container kind from its member names.

    Returns :data:`CONTAINER_LEGACY`, one of :data:`KNOWN_CATEGORIES`, or
    ``None`` when the layout matches neither format. The current-category
    signatures are strict (record §2's table) so that a damaged legacy
    archive falls through to the legacy pipeline and gets its precise
    required-files error instead of being misread as a category zip.
    """
    files = {n for n in names if not n.endswith("/")}
    root = {n for n in files if "/" not in n}

    if _LEGACY_TRIO <= root:
        return CONTAINER_LEGACY
    if {"users.json", "login_history.json"} <= root:
        return CATEGORY_LIGHT_METADATA
    if files == {"conversations.json"}:
        return CATEGORY_CONVERSATIONS
    if not root and files:
        if all(_PROJECT_MEMBER_RE.fullmatch(n) for n in files):
            return CATEGORY_PROJECTS
        if all(_MEMORY_MEMBER_RE.fullmatch(n) for n in files):
            return CATEGORY_MEMORIES
        # Later categories (export-categories record §3). artifact_data is
        # tested first: its members also sit under artifacts/<id>/, but it
        # never carries a descriptor, which frames requires.
        if all(_ARTIFACT_DATA_MEMBER_RE.fullmatch(n) for n in files):
            return CATEGORY_ARTIFACT_DATA
        if all(_ARTIFACT_MEMBER_RE.fullmatch(n) for n in files) and any(
            _ARTIFACT_DESCRIPTOR_RE.fullmatch(n) for n in files
        ):
            return CATEGORY_FRAMES
        if all(_CLAUDE_CODE_CLOUD_MEMBER_RE.fullmatch(n) for n in files):
            return CATEGORY_CLAUDE_CODE_CLOUD
    return None


def validate_category_zip(category: str, zf: zipfile.ZipFile) -> None:
    """Structural validation for the later categories (export-categories
    record §5.1): parse far enough to know the file can be read.

    Raises :class:`CategoryStructureError` naming nothing but the category;
    the routes skip the file with the D2 notice. Per-artifact surprises
    (missing files, hash mismatches, unreadable comments) are not structural —
    they are preserved and flagged when the category is displayed.
    """
    if category not in LATER_CATEGORIES:
        return
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if category == CATEGORY_FRAMES:
        targets = [n for n in names if _ARTIFACT_DESCRIPTOR_RE.fullmatch(n)]
        key = None
    elif category == CATEGORY_ARTIFACT_DATA:
        targets = names
        key = "documents"
    else:
        targets = [n for n in names if n.endswith(".json")]
        key = "claude_code_cloud"
    if not targets:
        raise CategoryStructureError(f"{category} file has nothing to read")
    for name in targets:
        try:
            with zf.open(name) as fh:
                payload = json.load(fh)
        except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, OSError) as e:
            raise CategoryStructureError(f"{category} member is not readable JSON") from e
        if not isinstance(payload, dict):
            raise CategoryStructureError(f"{category} member is not a JSON object")
        if key is not None and not isinstance(payload.get(key), list):
            raise CategoryStructureError(f"{category} member has no {key} list")


def sniff_manifest(data: bytes) -> bool:
    """True when ``data`` is a current-format manifest: JSON parsing to an
    object that contains ``data_files``. The manifest is metadata for the
    expected-vs-supplied banner only — its ``export_url`` links are live
    signed downloads and are **never fetched** (record §2).
    """
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(parsed, dict) and "data_files" in parsed


def manifest_file_count(data: bytes) -> int | None:
    """How many data files a manifest lists, or ``None`` when ``data`` is
    not a manifest — the expected-vs-supplied banner's ``{m}`` (record §4)."""
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    data_files = parsed.get("data_files")
    return len(data_files) if isinstance(data_files, list) else None


def manifest_categories(data: bytes) -> tuple[str, ...]:
    """The manifest's distinct ``category`` values in listed order — the
    expected categories for the degraded-view banner (export-categories
    record §4, A7). Empty when ``data`` is not a manifest."""
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return ()
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data_files"), list):
        return ()
    seen: list[str] = []
    for entry in parsed["data_files"]:
        category = entry.get("category") if isinstance(entry, dict) else None
        if isinstance(category, str) and category and category not in seen:
            seen.append(category)
    return tuple(seen)


@dataclass(frozen=True, slots=True)
class ManifestLink:
    """One listed data file for the manifest links page (export-categories
    record §8). ``url`` is the raw ``export_url`` — displayed, never fetched."""

    filename: str
    category: str
    url: str


@dataclass(frozen=True, slots=True)
class ManifestLinks:
    created_at: datetime | None
    files: list[ManifestLink]


def manifest_links(data: bytes) -> ManifestLinks | None:
    """Read a manifest's file list for display, entirely in memory — or
    ``None`` when ``data`` is not a manifest. The links are live signed
    downloads: the viewer shows them to the user and never dereferences
    them (record §8, amendment A8)."""
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data_files"), list):
        return None
    created_raw = parsed.get("created_at")
    created = _parse_dt(created_raw) if isinstance(created_raw, str) and created_raw else None
    files = []
    for entry in parsed["data_files"]:
        if not isinstance(entry, dict):
            continue
        files.append(
            ManifestLink(
                filename=str(entry.get("filename") or ""),
                category=str(entry.get("category") or ""),
                url=str(entry.get("export_url") or ""),
            )
        )
    return ManifestLinks(created_at=created, files=files)


def manifest_part_map(data: bytes) -> dict[str, int]:
    """``filename → part`` from a manifest's ``data_files`` entries.

    Part order is metadata only the manifest and the filename carry (record
    §4) — this is the manifest half, feeding the routes' same-category part
    grouping. Entries without a string ``filename`` and an integer ``part``
    are skipped; anything that isn't a manifest yields an empty map.
    """
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data_files"), list):
        return {}
    out: dict[str, int] = {}
    for entry in parsed["data_files"]:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("filename"), str)
            and isinstance(entry.get("part"), int)
            and not isinstance(entry.get("part"), bool)
        ):
            out[entry["filename"]] = entry["part"]
    return out


def _open_zip(path: Path) -> tuple[zipfile.ZipFile, set[str]]:
    if not path.is_file():
        raise ExportFormatError(f"export archive not found: {path}")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ExportFormatError(f"not a valid zip archive: {path} ({e})") from e
    return zf, set(zf.namelist())


def _sniff_zip_path(path: Path) -> str | None:
    zf, names = _open_zip(path)
    with zf:
        return sniff_container(names)


def _warn_if_isolated_batch(name: str) -> None:
    match = BATCH_FILENAME_RE.search(name)
    if match and match.group(1) != "0000":
        logger.warning(
            "loaded a single archive with batch index %s — earlier batches may be missing",
            match.group(1),
        )


# ---------------------------------------------------------------------------
# Current-format set loading (record §3): any subset of category zips,
# multi-part categories merged, everything normalised into ``Export``.
# ---------------------------------------------------------------------------


def _load_current_set(parts: list[tuple[str, zipfile.ZipFile, str, str]]) -> Export:
    """Assemble an ``Export`` from current-format category zips.

    ``parts`` holds ``(category, open zipfile, source_name, display_name)``
    in supplied order; every zipfile is closed before returning. There is no
    required category — each missing one degrades its features visibly,
    never fatally (record §3).

    Multi-part categories (*provisional — no multi-part export has been
    observed*): parts merge by concatenating their lists in supplied order.
    A conversation or project uuid appearing in more than one part is
    preserved once (first occurrence wins) and flagged with a ``WARNING``;
    multi-part memories must agree on ``conversations_memory`` (mirroring
    the legacy batch invariant) while ``project_memories`` merge first-wins
    and ``memory_files`` concatenate.
    """
    by_category: dict[str, list[tuple[zipfile.ZipFile, str, str]]] = {}
    source_paths: list[str] = []
    with ExitStack() as stack:
        for category, zf, source_name, display_name in parts:
            stack.enter_context(zf)
            by_category.setdefault(category, []).append((zf, source_name, display_name))
            source_paths.append(source_name)

        for category, cat_parts in by_category.items():
            _warn_if_parts_incomplete(category, [display for _, _, display in cat_parts])

        # Later categories (export-categories record §5.1): validated before
        # anything else reads them. A part that fails validation is skipped
        # with a WARNING rather than failing the set (the routes validate
        # first and show D2; this guards stored sets).
        for category in LATER_CATEGORIES:
            kept = []
            for zf, src, display in by_category.get(category, []):
                try:
                    validate_category_zip(category, zf)
                except CategoryStructureError:
                    logger.warning("skipped unreadable %s file: %s", category, display)
                    continue
                kept.append((zf, src, display))
            if kept:
                by_category[category] = kept
            else:
                by_category.pop(category, None)

        user: User | None = None
        login_events: list[LoginEvent] = []
        for zf, _src, _display in by_category.get(CATEGORY_LIGHT_METADATA, []):
            part_user = _load_users(zf)
            if user is None:
                user = part_user
            elif part_user != user:
                raise ExportFormatError(
                    f"users.json differs across parts: {user.uuid} vs {part_user.uuid}"
                )
            login_events.extend(_load_login_history(zf))

        global_memory: GlobalMemory | None = None
        project_memories: dict[str, ProjectMemory] = {}
        memory_files: list[MemoryFile] = []
        for zf, _src, _display in by_category.get(CATEGORY_MEMORIES, []):
            part_global, part_memories, part_files = _load_memories_category(zf)
            if global_memory is None:
                global_memory = part_global
            elif part_global.content != global_memory.content:
                raise ExportFormatError("conversation memory differs across memories parts")
            for puid, mem in part_memories.items():
                if puid in project_memories:
                    logger.warning("duplicate project memory across memories parts: %s", puid)
                    continue
                project_memories[puid] = mem
            memory_files.extend(part_files)

        if (
            user is not None
            and global_memory is not None
            and global_memory.account_uuid
            and global_memory.account_uuid != user.uuid
        ):
            logger.warning(
                "account uuid mismatch: users.json=%s, memories=%s",
                user.uuid,
                global_memory.account_uuid,
            )

        projects: list[Project] = []
        seen_proj: set[str] = set()
        for zf, _src, _display in by_category.get(CATEGORY_PROJECTS, []):
            for p in _load_project_files(zf, project_memories):
                if p.uuid in seen_proj:
                    logger.warning("duplicate project uuid across parts: %s", p.uuid)
                    continue
                seen_proj.add(p.uuid)
                projects.append(p)
        # Dangling-memory computation runs against the merged project list;
        # with no projects category supplied, every project memory dangles —
        # real content, surfaced on the Memory page rather than dropped
        # (preserve-and-flag).
        orphan_memories: list[ProjectMemory] = []
        for puid, mem in project_memories.items():
            if puid not in seen_proj:
                logger.warning("dangling project memory for uuid not in archive: %s", puid)
                orphan_memories.append(mem)

        artifacts, artifact_data, tasks, runs = _load_later_categories(by_category)
        if user is not None:
            for artifact in artifacts:
                if artifact.owner_account and artifact.owner_account != user.uuid:
                    # Mirrors the account-uuid check: logged, never fatal.
                    logger.warning(
                        "artifact owner mismatch on %s: owner %s, users.json %s",
                        artifact.id,
                        artifact.owner_account,
                        user.uuid,
                    )

        conversations: list[Conversation] = []
        seen_conv: set[str] = set()
        account_uuid = user.uuid if user is not None else ""
        for zf, _src, _display in by_category.get(CATEGORY_CONVERSATIONS, []):
            for conv in _load_conversations(zf, account_uuid):
                if conv.uuid in seen_conv:
                    logger.warning(
                        "duplicate conversation uuid across parts: %s — keeping the "
                        "first occurrence",
                        conv.uuid,
                    )
                    continue
                seen_conv.add(conv.uuid)
                conversations.append(conv)

    return Export(
        user=user,
        global_memory=global_memory,
        projects=projects,
        conversations=conversations,
        orphan_project_memories=orphan_memories,
        source_paths=source_paths,
        memory_files=memory_files,
        login_events=login_events,
        categories=tuple(c for c in KNOWN_CATEGORIES if c in by_category),
        artifacts=artifacts,
        artifact_data=artifact_data,
        scheduled_tasks=tasks,
        task_runs=runs,
        artifact_mentions=_artifact_mentions(conversations, {a.id for a in artifacts}),
    )


def _load_later_categories(
    by_category: dict[str, list[tuple[zipfile.ZipFile, str, str]]],
) -> tuple[list[Artifact], list[ArtifactDataFile], list[ScheduledTask], list[TaskRun]]:
    """Load the validated later-category parts (export-categories record §6).

    Multi-part ``frames`` merge like the other categories (*provisional*): parts in
    supplied order, a duplicate artifact id kept once (first occurrence) and flagged.
    """
    artifacts: list[Artifact] = []
    seen: set[str] = set()
    for zf, _src, display in by_category.get(CATEGORY_FRAMES, []):
        for artifact in load_frames(zf, display):
            if artifact.id in seen:
                logger.warning(
                    "duplicate artifact id across frames parts: %s — keeping the first",
                    artifact.id,
                )
                continue
            seen.add(artifact.id)
            artifacts.append(artifact)
    artifact_data: list[ArtifactDataFile] = []
    for zf, _src, _display in by_category.get(CATEGORY_ARTIFACT_DATA, []):
        artifact_data.extend(load_artifact_data(zf))
    orphan_data = sorted({d.artifact_id for d in artifact_data} - seen)
    if orphan_data:
        logger.warning(
            "artifact data for %d artifact(s) not in this export — preserved as orphans",
            len(orphan_data),
        )
    tasks: list[ScheduledTask] = []
    runs: list[TaskRun] = []
    for zf, _src, _display in by_category.get(CATEGORY_CLAUDE_CODE_CLOUD, []):
        part_tasks, part_runs = load_claude_code_cloud(zf)
        tasks.extend(part_tasks)
        runs.extend(part_runs)
    known = {t.routine_id for t in tasks}
    absent = [r for r in runs if r.routine_id not in known]
    if absent:
        logger.warning(
            "%d scheduled-task run(s) name a task not in this export — preserved as orphans",
            len(absent),
        )
    return artifacts, artifact_data, tasks, runs


def _artifact_mentions(conversations: list[Conversation], known: set[str]) -> dict[str, list[str]]:
    """Record §6.5: an artifact is mentioned by a conversation when its id appears in
    a tool block's ``input`` or ``structured_content``. Prose is not used."""
    mentions: dict[str, list[str]] = {}
    if not known:
        return mentions
    for conv in conversations:
        for msg in conv.messages:
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    ids = artifact_ids_in(block.input, known)
                elif isinstance(block, ToolResultBlock):
                    ids = artifact_ids_in(block.structured_content, known)
                else:
                    continue
                for aid in ids:
                    convs = mentions.setdefault(aid, [])
                    if conv.uuid not in convs:
                        convs.append(conv.uuid)
    return mentions


def _warn_if_parts_incomplete(category: str, display_names: list[str]) -> None:
    """Warn when a category's part numbering suggests missing parts.

    Filename-derived and warning-only (record §2: filenames never decide
    parsing). Files without the ``-NNN`` suffix contribute nothing.
    """
    numbers = []
    for name in display_names:
        m = PART_FILENAME_RE.search(name)
        if m:
            numbers.append(int(m.group(1)))
    if not numbers:
        return
    expected = set(range(max(numbers) + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        logger.warning(
            "category %s parts look incomplete — missing part number(s): %s",
            category,
            ", ".join(f"{n:03d}" for n in missing),
        )


def _load_login_history(zf: zipfile.ZipFile) -> list[LoginEvent]:
    if "login_history.json" not in zf.namelist():
        logger.warning("light_metadata archive has no login_history.json — no login history")
        return []
    raw = _read_json(zf, "login_history.json")
    events = raw.get("login_events") if isinstance(raw, dict) else None
    if not isinstance(events, list):
        logger.warning("login_history.json has no login_events list — no login history")
        return []
    parsed: list[LoginEvent] = []
    for rec in events:
        if not isinstance(rec, dict):
            logger.warning("skipping non-dict login event")
            continue
        location = rec.get("location_info") or {}
        agent = rec.get("user_agent") or {}
        parsed.append(
            LoginEvent(
                timestamp=_parse_dt(rec.get("timestamp")),
                method=rec.get("method", "") or "",
                ip_address=rec.get("ip_address", "") or "",
                city=location.get("city"),
                region=location.get("region"),
                country=location.get("country"),
                browser_family=agent.get("browser_family"),
                browser_version=agent.get("browser_version"),
                device_family=agent.get("device_family"),
                os_family=agent.get("os_family"),
                os_version=agent.get("os_version"),
            )
        )
    return parsed


def _load_memories_category(
    zf: zipfile.ZipFile,
) -> tuple[GlobalMemory, dict[str, ProjectMemory], list[MemoryFile]]:
    """Read the current format's single ``memories/<uuid>.json`` dict."""
    members = [n for n in zf.namelist() if _MEMORY_MEMBER_RE.fullmatch(n)]
    if len(members) != 1:
        raise ExportFormatError(
            f"memories archive must contain exactly one memories/<uuid>.json, got {len(members)}"
        )
    rec = _read_json(zf, members[0])
    if not isinstance(rec, dict):
        raise ExportFormatError(f"{members[0]} must be a JSON object")
    return _parse_memories_record(rec)


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
        # The legacy container's required trio — per-container by design:
        # the current format has no required file at all (record §3).
        for required in ("users.json", "memories.json", "conversations.json"):
            if required not in names:
                raise ExportFormatError(f"missing required file: {required}")

        user = _load_users(zf)
        global_memory, project_memories_by_uuid, memory_files = _load_memories(zf)

        if global_memory.account_uuid and global_memory.account_uuid != user.uuid:
            logger.warning(
                "account uuid mismatch: users.json=%s, memories.json=%s",
                user.uuid,
                global_memory.account_uuid,
            )

        projects, orphan_memories = _load_projects(zf, project_memories_by_uuid)
        conversations = _load_conversations(zf, user.uuid)
        # Later legacy archives carry the current format's login history at
        # the root (export-categories record §5.4, V21) — same model slot.
        login_events = _load_login_history(zf) if "login_history.json" in names else []

    return Export(
        user=user,
        global_memory=global_memory,
        projects=projects,
        conversations=conversations,
        orphan_project_memories=orphan_memories,
        source_paths=[source_name],
        memory_files=memory_files,
        login_events=login_events,
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
        verified_phone_number=rec.get("verified_phone_number", "") or "",
    )


# ---------------------------------------------------------------------------
# memories.json
# ---------------------------------------------------------------------------


def _load_memories(
    zf: zipfile.ZipFile,
) -> tuple[GlobalMemory, dict[str, ProjectMemory], list[MemoryFile]]:
    """Legacy ``memories.json``: a list of exactly one record."""
    raw = _read_json(zf, "memories.json")
    if not isinstance(raw, list) or len(raw) != 1:
        raise ExportFormatError("memories.json must contain exactly one record")
    rec = raw[0]
    if not isinstance(rec, dict):
        raise ExportFormatError("memories.json record must be a dict")
    return _parse_memories_record(rec)


def _parse_memories_record(
    rec: dict[str, Any],
) -> tuple[GlobalMemory, dict[str, ProjectMemory], list[MemoryFile]]:
    """Shared body for both memories shapes — the legacy list-of-1 record and
    the current format's ``memories/<uuid>.json`` dict. ``memory_files`` is
    current-only; a legacy record simply has none."""
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
    memory_files: list[MemoryFile] = []
    for f in rec.get("memory_files") or []:
        if not isinstance(f, dict):
            logger.warning("skipping non-dict memory_files entry")
            continue
        memory_files.append(
            MemoryFile(
                path=f.get("path", "") or "",
                content=f.get("content", "") or "",
                updated_at=_parse_dt(f.get("updated_at")),
            )
        )
    return global_memory, project_memories, memory_files


# ---------------------------------------------------------------------------
# projects/<uuid>.json
# ---------------------------------------------------------------------------


def _load_projects(
    zf: zipfile.ZipFile,
    project_memories: dict[str, ProjectMemory],
) -> tuple[list[Project], list[ProjectMemory]]:
    projects = _load_project_files(zf, project_memories)
    seen_uuids = {p.uuid for p in projects}

    orphan_memories: list[ProjectMemory] = []
    for puid, mem in project_memories.items():
        if puid not in seen_uuids:
            logger.warning("dangling project memory for uuid not in archive: %s", puid)
            orphan_memories.append(mem)

    return projects, orphan_memories


def _load_project_files(
    zf: zipfile.ZipFile,
    project_memories: dict[str, ProjectMemory],
) -> list[Project]:
    """Parse every ``projects/<uuid>.json`` member — shared by both container
    pipelines; dangling-memory computation stays with the callers (the
    current loader runs it only after merging all parts)."""
    project_files = sorted(
        n for n in zf.namelist() if n.startswith("projects/") and n.endswith(".json")
    )

    projects: list[Project] = []
    for name in project_files:
        raw = _read_json(zf, name)
        if not isinstance(raw, dict):
            logger.warning("skipping malformed project file: %s", name)
            continue
        puid = raw.get("uuid")
        if not puid:
            logger.warning("project file %s missing uuid; skipping", name)
            continue
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
    return projects


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

    parsed_content: list[ContentBlock] = [
        _parse_content_block(blk, seen_warnings) for blk in raw.get("content") or []
    ]

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


def _parse_content_block(raw: dict[str, Any], seen_warnings: set[str]) -> ContentBlock:
    block_type = raw.get("type")

    if block_type == "text":
        return TextBlock(
            text=raw.get("text", "") or "",
            citations=raw.get("citations") or [],
            start_timestamp=raw.get("start_timestamp"),
            stop_timestamp=raw.get("stop_timestamp"),
        )
    if block_type == "thinking":
        # ``signature`` is deliberately not read: opaque attestation blobs
        # with no render or export use, excluded from model and cache — the
        # decision of record (design record §6, disclosed in help).
        return ThinkingBlock(
            thinking=raw.get("thinking", "") or "",
            summaries=_parse_summaries(raw.get("summaries"), seen_warnings),
            truncated=bool(raw.get("truncated")),
            cut_off=bool(raw.get("cut_off")),
            thinking_hidden=bool(raw.get("thinking_hidden")),
            start_timestamp=raw.get("start_timestamp"),
            stop_timestamp=raw.get("stop_timestamp"),
        )
    if block_type == "tool_use":
        name = raw.get("name", "") or ""
        return ToolUseBlock(
            id=raw.get("id", "") or "",
            name=name,
            input=raw.get("input") or {},
            integration_name=raw.get("integration_name"),
            is_mcp=bool(raw.get("is_mcp_app")) or ":" in name,
            display_content=raw.get("display_content"),
            message=_optional_str(raw.get("message")),
            start_timestamp=raw.get("start_timestamp"),
            stop_timestamp=raw.get("stop_timestamp"),
        )
    if block_type == "tool_result":
        meta = raw.get("meta") or {}
        return ToolResultBlock(
            tool_use_id=raw.get("tool_use_id", "") or "",
            name=raw.get("name", "") or "",
            is_error=bool(raw.get("is_error")),
            inner=raw.get("content") or [],
            display_content=raw.get("display_content"),
            output_format_category=(
                meta.get("output_format_category") if isinstance(meta, dict) else None
            ),
            message=_optional_str(raw.get("message")),
            structured_content=raw.get("structured_content"),
            start_timestamp=raw.get("start_timestamp"),
            stop_timestamp=raw.get("stop_timestamp"),
        )
    if block_type == "injected_prompt_block":
        return InjectedPromptBlock(
            prompt=raw.get("prompt", "") or "",
            initial_turn_only=bool(raw.get("initial_turn_only")),
            skip_on_truncated_continuation=bool(raw.get("skip_on_truncated_continuation")),
            start_timestamp=raw.get("start_timestamp"),
            stop_timestamp=raw.get("stop_timestamp"),
        )

    if block_type == "document":
        title = raw.get("title")
        return DocumentBlock(
            file_uuid=str(raw.get("file_uuid") or ""),
            title=title if isinstance(title, str) and title else None,
            flags=raw.get("flags"),
            start_timestamp=raw.get("start_timestamp"),
            stop_timestamp=raw.get("stop_timestamp"),
        )

    # Preserve-and-flag (house rule): the block travels with the message as
    # an UnknownBlock so the reading view and the export can render their
    # named placeholders, instead of the content silently vanishing.
    key = f"unknown_block:{block_type}"
    if key not in seen_warnings:
        logger.warning("unknown content block type %r — preserved as UnknownBlock", block_type)
        seen_warnings.add(key)
    return UnknownBlock(type=str(block_type) if block_type is not None else "", raw=raw)


def _optional_str(value: Any) -> str | None:
    """A non-empty string, or ``None`` for anything else."""
    return value if isinstance(value, str) and value else None


def _parse_summaries(raw: Any, seen_warnings: set[str]) -> list[str]:
    """Extract thinking-summary strings from the export's ``{summary: …}``
    items (record §6). Unexpected item shapes are preserved as their string
    form with a warning rather than dropped."""
    summaries: list[str] = []
    for item in raw or []:
        if isinstance(item, dict) and "summary" in item:
            summaries.append(str(item["summary"] or ""))
        elif isinstance(item, str):
            summaries.append(item)
        else:
            if "summary_shape" not in seen_warnings:
                logger.warning(
                    "unexpected thinking-summary item shape %r — preserved as text",
                    type(item).__name__,
                )
                seen_warnings.add("summary_shape")
            summaries.append(str(item))
    return summaries


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

    user = (
        f"{export.user.full_name} <{export.user.email_address}>"
        if export.user is not None
        else "(no account file in set)"
    )
    print(f"Sources       : {', '.join(export.source_paths)}")
    print(f"User          : {user}")
    print(f"Conversations : {len(export.conversations)}")
    print(f"Messages      : {total_msgs}")
    print(f"Branched convs: {branched}")
    print(f"Projects      : {len(export.projects)}")
    print(f"Proj memories : {project_memories}")
    print(f"Orphan mems   : {len(export.orphan_project_memories)}")


if __name__ == "__main__":
    _main()
