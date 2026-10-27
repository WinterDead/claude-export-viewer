"""Profile the JSON schema of Claude.ai data exports, emitting aggregates only.

Usage (the path is any directory outside the project tree)::

    python tools/scan_export_schema.py <path> [--checks-only] [--json]

``<path>`` may be:

- a legacy single-zip export (any ZIP holding ``conversations.json`` etc.);
- a current-format export set — a directory holding the manifest ``.json`` and the
  category zips (``conversations-000.zip``, ``frames-000.zip``, …); any subset may be
  present. A bare manifest ``.json`` path also works;
- a directory holding **several** exports: legacy ZIPs at its root and sets in its
  immediate subdirectories. Each export is profiled separately and labelled by position
  and kind only — directory and file names are never printed.

Multi-part category files (``conversations-001.zip``, …) are grouped and scanned as one
category (structural tolerance; no multi-part archive has been observed).

For every JSON document the scanner reports a schema profile: each path, its observed
value types with counts, optional-key presence ratios for dicts, and min/max/avg lengths
for strings and lists. It then reports derived **checks** — named counts that verify the
layouts the design records rely on (``docs/export-categories-design.md`` §2). Content
never appears in the output — privacy is by construction, not operator discipline:

- Verbatim values are printed only for the allowlisted machine-enum fields
  (``ENUM_FIELDS`` everywhere, ``SCOPED_ENUM_PATHS`` at named paths only), only when the
  value passes the machine-enum shape check (short single token, no whitespace, no
  sentence punctuation), and only when the field has few distinct values across the
  whole scan (``ENUM_CARDINALITY_CAP``); past the cap only the distinct count prints.
  ``source`` is deliberately absent from the allowlist: ``display_content.link.source``
  carries website names.
- UUID-shaped strings are masked everywhere: enum values, dictionary keys used as path
  segments, ZIP member names, and input filenames. Artifact version ids
  (``<unix-seconds>-<4 hex>``) are masked as ``<version-id>``.
- Dictionary keys that do not look like machine identifiers are masked in paths. Keys
  under artifact data's open-set ``data`` object are never printed at all.
- ZIP member paths below the category level of ``frames`` and ``artifact_data`` print a
  segment only when it is structural — it recurs across two or more artifacts —
  otherwise the segment is masked, keeping only its extension. Files inside an artifact
  version are counted, sized, and integrity-checked, never schema-profiled or named.
- ``memory_files[].path`` values get a census of their top-level segment only.
- All other string handling is length/count statistics only (including counts of empty
  values and of strings opening with a ``---`` frontmatter fence). Titles, names,
  prompts, bodies, comment text, and collection names are only ever measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Fields whose values are machine enums and may be printed verbatim (shape check still
# applies). Everything else is masked or reduced to statistics.
ENUM_FIELDS = frozenset(
    {
        "type",
        "sender",
        "category",
        "part",
        "batch_index",
        "method",
        "tool_origin",
        "output_format_category",
        "resource_type",
        "search_provider",
        "content_type",
        "citations_grouping_mode",
    }
)

# Enum fields printed only at these exact schema paths (any document), because the key
# name alone is too generic to allowlist everywhere (tool inputs are an open set).
SCOPED_ENUM_PATHS = frozenset(
    {
        "$.version",  # manifest
        "$.kind",  # frames artifact.json
        "$.visibility",  # frames artifact.json
        "$.files[].mime_type",  # frames <version-id>.files.json
        "$.claude_code_cloud[].status",  # routine_runs.json
    }
)
# Path suffixes with the same treatment (conversations nest under a variable prefix).
SCOPED_ENUM_SUFFIXES = (".attachments[].file_type",)

# A field prints its value census only when its distinct values across the whole scan
# number at most this many; otherwise only the distinct count prints.
ENUM_CARDINALITY_CAP = 12

VERSION_ID_RE = re.compile(r"^(\d{9,11})-([0-9a-f]{4})$")
EXTENSION_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")

# Machine-enum shape: one short token, no whitespace, no sentence punctuation.
ENUM_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,47}$")

# Dict keys that may appear verbatim as path segments. Keys are normally field names;
# anything else (content-derived keys) is masked.
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")

CATEGORY_ZIP_RE = re.compile(r"^(?P<category>[A-Za-z0-9_]+)-(?P<part>\d{3})\.zip$")

# Distinct-value tracking per path is capped; beyond the cap the report shows "N+".
DISTINCT_CAP = 1000


def mask_uuids(text: str) -> str:
    """Replace every UUID-shaped substring with ``<uuid>``."""
    return UUID_RE.sub("<uuid>", text)


def path_segment(key: str) -> str:
    """Return the display form of a dict key used as a schema-path segment."""
    if UUID_RE.fullmatch(key):
        return "<uuid>"
    if SAFE_KEY_RE.match(key):
        return key
    return "<masked-key>"


def census_display(value: Any) -> str:
    """Return the safe display form of an allowlisted enum field's value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        if UUID_RE.fullmatch(value):
            return "<uuid>"
        if ENUM_VALUE_RE.fullmatch(value) and not value.endswith("."):
            return value
        return f"<masked len={len(value)}>"
    return f"<{type(value).__name__}>"


class _PathStats:
    """Accumulated statistics for one schema path within one document group."""

    __slots__ = (
        "types",
        "dict_count",
        "key_presence",
        "str_stats",
        "list_stats",
        "str_empty",
        "list_empty",
        "frontmatter",
        "distinct",
        "distinct_overflow",
        "uuid_shaped",
        "timestamp_shaped",
        "bool_counts",
        "census",
        "top_segments",
    )

    def __init__(self) -> None:
        self.types: Counter[str] = Counter()
        self.dict_count = 0
        self.key_presence: Counter[str] = Counter()
        self.str_stats: list[int] = [0, 0, -1, -1]  # n, total, min, max
        self.list_stats: list[int] = [0, 0, -1, -1]
        self.str_empty = 0
        self.list_empty = 0
        self.frontmatter = 0
        self.distinct: set[int] = set()
        self.distinct_overflow = False
        self.uuid_shaped = 0
        self.timestamp_shaped = 0
        self.bool_counts: Counter[str] = Counter()
        self.census: Counter[str] = Counter()
        self.top_segments: Counter[str] = Counter()

    @staticmethod
    def _push(stats: list[int], value: int) -> None:
        stats[0] += 1
        stats[1] += value
        stats[2] = value if stats[2] < 0 else min(stats[2], value)
        stats[3] = max(stats[3], value)

    @staticmethod
    def _length_summary(stats: list[int]) -> dict[str, int | float] | None:
        n, total, lo, hi = stats
        if n == 0:
            return None
        return {"n": n, "min": lo, "max": hi, "avg": round(total / n, 1)}

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"types": dict(self.types)}
        if self.dict_count:
            out["dict_keys"] = {
                key: {"present": count, "of": self.dict_count}
                for key, count in sorted(self.key_presence.items())
            }
        strings = self._length_summary(self.str_stats)
        if strings is not None:
            distinct = len(self.distinct)
            strings["distinct"] = f"{distinct}+" if self.distinct_overflow else distinct
            if self.str_empty:
                strings["empty"] = self.str_empty
            if self.frontmatter:
                strings["frontmatter"] = self.frontmatter
            if self.uuid_shaped:
                strings["uuid_shaped"] = self.uuid_shaped
            if self.timestamp_shaped:
                strings["timestamp_shaped"] = self.timestamp_shaped
            out["str_len"] = strings
        lists = self._length_summary(self.list_stats)
        if lists is not None:
            if self.list_empty:
                lists["empty"] = self.list_empty
            out["list_len"] = lists
        if self.bool_counts:
            out["bool_values"] = dict(self.bool_counts)
        if self.census:
            out["value_census"] = dict(self.census.most_common())
        if self.top_segments:
            out["top_segment_census"] = dict(self.top_segments.most_common())
        return out


class SchemaProfiler:
    """Walks JSON values and accumulates per-path aggregate statistics."""

    def __init__(self, opaque_paths: frozenset[str] = frozenset()) -> None:
        self.paths: defaultdict[str, _PathStats] = defaultdict(_PathStats)
        # Paths whose dict values are open user-defined sets: their key count is
        # measured, their keys and children never walked or printed.
        self.opaque_paths = opaque_paths

    def walk(self, value: Any, path: str = "$", key: str | None = None) -> None:
        stats = self.paths[path]

        if value is None:
            stats.types["null"] += 1
        elif isinstance(value, bool):
            stats.types["bool"] += 1
            stats.bool_counts["true" if value else "false"] += 1
        elif isinstance(value, int | float):
            stats.types["number"] += 1
        elif isinstance(value, str):
            stats.types["str"] += 1
            self._record_string(stats, value)
            if path.endswith(".memory_files[].path"):
                self._record_top_segment(stats, value)
        elif isinstance(value, list):
            stats.types["list"] += 1
            self._push_stat(stats.list_stats, len(value))
            if not value:
                stats.list_empty += 1
            child_path = f"{path}[]"
            for item in value:
                self.walk(item, child_path, key)
        elif isinstance(value, dict) and path in self.opaque_paths:
            stats.types["dict(opaque)"] += 1
            self._push_stat(stats.list_stats, len(value))
        elif isinstance(value, dict):
            stats.types["dict"] += 1
            stats.dict_count += 1
            for child_key, child_value in value.items():
                segment = path_segment(child_key)
                stats.key_presence[segment] += 1
                self.walk(child_value, f"{path}.{segment}", child_key)
        else:  # pragma: no cover - json.load never yields other types
            stats.types[type(value).__name__] += 1

        if not isinstance(value, dict | list) and (
            key in ENUM_FIELDS or path in SCOPED_ENUM_PATHS or path.endswith(SCOPED_ENUM_SUFFIXES)
        ):
            stats.census[census_display(value)] += 1

    @staticmethod
    def _push_stat(stat_slot: list[int], value: int) -> None:
        _PathStats._push(stat_slot, value)

    def _record_string(self, stats: _PathStats, value: str) -> None:
        self._push_stat(stats.str_stats, len(value))
        if not value:
            stats.str_empty += 1
        elif value.startswith(("---\n", "---\r\n")):
            stats.frontmatter += 1
        if len(stats.distinct) < DISTINCT_CAP:
            stats.distinct.add(hash(value))
        elif hash(value) not in stats.distinct:
            stats.distinct_overflow = True
        if UUID_RE.fullmatch(value):
            stats.uuid_shaped += 1
        elif TIMESTAMP_RE.match(value):
            stats.timestamp_shaped += 1

    @staticmethod
    def _record_top_segment(stats: _PathStats, value: str) -> None:
        """Census the top-level segment of a memory-file path; deeper slugs never print.

        A leading ``/`` is kept in the display so the census also records whether paths
        are written absolute-style.
        """
        lead = "/" if value.startswith("/") else ""
        stripped = value.lstrip("/")
        if "/" in stripped:
            segment, _, _ = stripped.partition("/")
            stats.top_segments[f"{lead}{census_display(segment)}/"] += 1
        else:
            stats.top_segments[f"{lead}{census_display(stripped)}"] += 1

    def summary(self) -> dict[str, dict[str, Any]]:
        return {path: self.paths[path].summary() for path in sorted(self.paths)}


def _member_display(name: str) -> str:
    """Mask a ZIP member name for display and grouping."""
    parts = []
    for raw in name.split("/"):
        stem, dot, suffix = raw.rpartition(".")
        if dot and UUID_RE.fullmatch(stem):
            parts.append(f"<uuid>.{suffix}")
        else:
            parts.append(path_segment(raw) if raw else raw)
    return "/".join(parts)


def _new_group(opaque_paths: frozenset[str] = frozenset()) -> dict[str, Any]:
    return {
        "files": 0,
        "compressed_bytes": 0,
        "uncompressed_bytes": 0,
        "profiler": SchemaProfiler(opaque_paths),
    }


def _profile_payload(
    documents: dict[str, dict[str, Any]],
    display: str,
    payload: Any,
    compressed: int,
    uncompressed: int,
    opaque_paths: frozenset[str] = frozenset(),
) -> None:
    group = documents.setdefault(display, _new_group(opaque_paths))
    group["files"] += 1
    group["compressed_bytes"] += compressed
    group["uncompressed_bytes"] += uncompressed
    group["profiler"].walk(payload)


def _load_member_json(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Any:
    with zf.open(info) as fh:
        return json.load(fh)


def scan_zip(
    zip_path: Path,
    documents: dict[str, dict[str, Any]],
    errors: list[str],
    analysis: ExportAnalysis | None = None,
) -> None:
    """Profile every JSON member of one plain ZIP (legacy archive or one of the
    original four categories) into the shared document-group map."""
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".json"):
                continue
            display = _member_display(info.filename)
            try:
                payload = _load_member_json(zf, info)
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(f"{display}: not parseable JSON ({type(exc).__name__})")
                continue
            _profile_payload(documents, display, payload, info.compress_size, info.file_size)
            if analysis is not None:
                analysis.observe_json(info.filename, payload, names)


def scan_json_file(
    json_path: Path,
    documents: dict[str, dict[str, Any]],
    errors: list[str],
    analysis: ExportAnalysis | None = None,
) -> None:
    size = json_path.stat().st_size
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        errors.append(f"(manifest): not parseable JSON ({type(exc).__name__})")
        return
    _profile_payload(documents, "(manifest)", payload, size, size)
    if analysis is not None:
        analysis.observe_manifest(payload)


# ---------------------------------------------------------------------------
# Derived checks — the verification table's rows as named counts.
# ---------------------------------------------------------------------------


def _epoch(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _sortable(value: Any) -> str:
    return value if isinstance(value, str) else ""


class _EntryHtmlProbe(HTMLParser):
    """Counts the structural features of an artifact's entry HTML; no text kept."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts = 0
        self.external = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts += 1
        for name, value in attrs:
            if (
                name in {"src", "href"}
                and value
                and value.lower().startswith(("http://", "https://", "//"))
            ):
                self.external += 1


def _probe_html(data: bytes) -> tuple[bool, int, int]:
    text = data.decode("utf-8", errors="replace")
    probe = _EntryHtmlProbe()
    try:
        probe.feed(text)
        probe.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient
        pass
    external_css = len(re.findall(r"url\(\s*['\"]?(?:https?:)?//", text, re.IGNORECASE))
    external_css += len(re.findall(r"@import\s+['\"](?:https?:)?//", text, re.IGNORECASE))
    has_doctype = text.lstrip().lower().startswith("<!doctype")
    return has_doctype, probe.scripts, probe.external + external_css


def _is_capability_config(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"v", "source", "db"}:
        return False
    db = payload.get("db")
    return (
        payload.get("source") == "db"
        and isinstance(db, dict)
        and "meta" in db
        and isinstance(db.get("collections"), list)
    )


def _mask_segment(segment: str, structural: set[str]) -> str:
    """Display form of one member-path segment below the category level."""
    if UUID_RE.fullmatch(segment):
        return "<uuid>"
    for suffix in (".files.json", ".html", ""):
        stem = segment[: -len(suffix)] if suffix and segment.endswith(suffix) else segment
        if (suffix == "" or segment.endswith(suffix)) and VERSION_ID_RE.fullmatch(stem):
            return f"<version-id>{suffix}"
    if segment in structural and SAFE_KEY_RE.fullmatch(segment):
        return segment
    _stem, dot, ext = segment.rpartition(".")
    if dot and _stem and EXTENSION_RE.fullmatch(ext):
        return f"<masked>.{ext.lower()}"
    return "<masked>"


class ExportAnalysis:
    """Accumulates the verification-table checks for one export.

    Every value recorded is a count, a boolean outcome, or (for the cross-export
    memory comparison only) a digest that is compared and never printed.
    """

    def __init__(self) -> None:
        self.checks: Counter[str] = Counter()
        self.censuses: dict[str, Counter[str]] = defaultdict(Counter)
        self.artifact_ids: set[str] = set()
        self.data_artifact_ids: set[str] = set()
        self.linked_ids_structured: set[str] = set()
        self.linked_ids_container: set[str] = set()
        self.uuids_in_result_text: set[str] = set()
        # Every UUID found anywhere in a tool input or structured_content — the
        # record's section 6.5 linking rule, compared against frames ids at the end.
        self.uuids_in_tool_fields: set[str] = set()
        self.routine_ids: set[str] = set()
        self.run_routine_ids: list[str] = []
        self.conversations_memory_digest: str | None = None
        self.memory_files_digest: str | None = None

    # -- manifest (V1) ----------------------------------------------------

    def observe_manifest(self, payload: Any) -> None:
        if not isinstance(payload, dict) or "data_files" not in payload:
            return
        self.checks["V1 manifests"] += 1
        files = payload.get("data_files")
        if isinstance(files, list):
            self.checks["V1 manifest data_files entries"] += len(files)
            if payload.get("total_files") == len(files):
                self.checks["V1 manifest total_files equals list length"] += 1
            else:
                self.checks["V1 manifest total_files differs from list length"] += 1

    # -- plain JSON members (memories, conversations, legacy root) -------

    def observe_json(self, member: str, payload: Any, names: set[str]) -> None:
        if member == "login_history.json" and "users.json" in names:
            if "conversations.json" in names:
                self.checks["V21 legacy archive with root login_history.json"] += 1
                shape = isinstance(payload, dict) and isinstance(payload.get("login_events"), list)
                key = "V21 legacy login_history {login_events: [...]} shape"
                self.checks[f"{key} {'yes' if shape else 'no'}"] += 1
        if member == "memories.json" or re.fullmatch(r"memories/[^/]+\.json", member):
            record = payload[0] if isinstance(payload, list) and payload else payload
            if isinstance(record, dict):
                memory = record.get("conversations_memory")
                if memory is not None:
                    self.conversations_memory_digest = hashlib.sha256(
                        json.dumps(memory, sort_keys=True).encode("utf-8")
                    ).hexdigest()
                if "memory_files" in record:
                    self.memory_files_digest = hashlib.sha256(
                        json.dumps(record["memory_files"], sort_keys=True).encode("utf-8")
                    ).hexdigest()
                    files = record["memory_files"]
                    self.checks["V20 memory_files entries"] += (
                        len(files) if isinstance(files, list) else 0
                    )
        if member == "conversations.json" and isinstance(payload, list):
            self._observe_conversations(payload)

    def _observe_conversations(self, conversations: list[Any]) -> None:
        for conv in conversations:
            if not isinstance(conv, dict):
                continue
            for message in conv.get("chat_messages") or []:
                if not isinstance(message, dict):
                    continue
                sender = message.get("sender")
                for block in message.get("content") or []:
                    if isinstance(block, dict):
                        self._observe_block(block, sender)
                for attachment in message.get("attachments") or []:
                    if isinstance(attachment, dict) and "file_type" in attachment:
                        self.checks["V19 attachments with file_type"] += 1

    def _observe_block(self, block: dict[str, Any], sender: Any) -> None:
        block_type = block.get("type")
        if "title" in block:
            self.checks["V19 blocks with block-level title"] += 1
        if "file_uuid" in block:
            self.checks["V19 blocks with block-level file_uuid"] += 1
        meta = block.get("meta")
        if isinstance(meta, dict) and "tool_references" in meta:
            self.checks["V19 blocks with meta.tool_references"] += 1
        if block_type == "document":
            self.checks["V18 document blocks"] += 1
            self.censuses["V18 document block sender"][census_display(sender)] += 1
            if not block.get("title"):
                self.checks["V18 document blocks without title"] += 1
            for key in block:
                self.censuses["V18 document block keys"][path_segment(key)] += 1
        if block_type == "tool_use":
            if block.get("name") == "Artifact":
                self.checks["V17 tool_use blocks named Artifact"] += 1
            tool_input = block.get("input")
            self.uuids_in_tool_fields.update(UUID_RE.findall(json.dumps(tool_input)))
            container = tool_input.get("container") if isinstance(tool_input, dict) else None
            if isinstance(container, dict) and isinstance(container.get("id"), str):
                self.checks["V17 tool inputs with container.id"] += 1
                self.linked_ids_container.add(container["id"])
        if block_type == "tool_result":
            structured = block.get("structured_content")
            self.uuids_in_tool_fields.update(UUID_RE.findall(json.dumps(structured)))
            if isinstance(structured, dict):
                if isinstance(structured.get("artifact_id"), str):
                    self.checks["V17 tool results with structured_content.artifact_id"] += 1
                    self.linked_ids_structured.add(structured["artifact_id"])
                if "url" in structured:
                    self.checks["V17 tool results with structured_content.url"] += 1
            for inner in block.get("content") or []:
                if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                    self.uuids_in_result_text.update(UUID_RE.findall(inner["text"]))

    # -- frames (V3–V11, V13) --------------------------------------------

    def scan_frames(
        self, zip_path: Path, documents: dict[str, dict[str, Any]], errors: list[str]
    ) -> None:
        with zipfile.ZipFile(zip_path) as zf:
            by_artifact: dict[str, dict[str, zipfile.ZipInfo]] = defaultdict(dict)
            for info in zf.infolist():
                if info.is_dir():
                    continue
                match = re.fullmatch(r"artifacts/([^/]+)/(.+)", info.filename)
                if not match:
                    self.checks["V3 frames members outside artifacts/<id>/"] += 1
                    continue
                by_artifact[match.group(1)][match.group(2)] = info

            segment_owners: defaultdict[str, set[str]] = defaultdict(set)
            for aid, members in by_artifact.items():
                for rest in members:
                    for segment in rest.split("/"):
                        segment_owners[segment].add(aid)
            structural = {seg for seg, owners in segment_owners.items() if len(owners) >= 2}
            for members in by_artifact.values():
                for rest in members:
                    shown = "/".join(_mask_segment(seg, structural) for seg in rest.split("/"))
                    self.censuses["V3 frames member layout"][f"artifacts/<uuid>/{shown}"] += 1

            for aid, members in sorted(by_artifact.items()):
                self._scan_artifact(zf, aid, members, documents, errors)

    def _scan_artifact(
        self,
        zf: zipfile.ZipFile,
        aid: str,
        members: dict[str, zipfile.ZipInfo],
        documents: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> None:
        self.checks["V3 artifact directories"] += 1
        if aid in self.artifact_ids:
            self.checks["V3 artifact ids repeated across parts"] += 1
        self.artifact_ids.add(aid)
        if not UUID_RE.fullmatch(aid):
            self.checks["V4 artifact directory names not uuid-shaped"] += 1
        info = members.get("artifact.json")
        if info is None:
            self.checks["V3 artifact directories without artifact.json"] += 1
            return
        try:
            descriptor = _load_member_json(zf, info)
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(f"artifacts/<uuid>/artifact.json: not parseable ({type(exc).__name__})")
            return
        _profile_payload(
            documents,
            "artifacts/<uuid>/artifact.json",
            descriptor,
            info.compress_size,
            info.file_size,
        )
        if not isinstance(descriptor, dict):
            self.checks["V4 artifact.json not an object"] += 1
            return
        self.checks[f"V4 artifact.json id equals directory {descriptor.get('id') == aid}"] += 1

        versions = [v for v in descriptor.get("versions") or [] if isinstance(v, dict)]
        version_ids = [v.get("id") for v in versions]
        self.checks["V4 versions listed"] += len(versions)
        for version in versions:
            vid = version.get("id")
            match = VERSION_ID_RE.fullmatch(vid) if isinstance(vid, str) else None
            self.checks[f"V6 version id shape <seconds>-<4 hex> {bool(match)}"] += 1
            if match:
                same = int(match.group(1)) == _epoch(version.get("created_at"))
                self.checks[f"V6 version id seconds equal created_at {same}"] += 1

        created = [_sortable(v.get("created_at")) for v in versions]
        if len(versions) > 1:
            if created == sorted(created):
                order = "ascending"
            elif created == sorted(created, reverse=True):
                order = "descending"
            else:
                order = "unordered"
            self.checks[f"V7 multi-version lists in {order} created_at order"] += 1
        active = descriptor.get("active_version")
        self.checks[f"V7 active_version in versions list {active in version_ids}"] += 1
        if versions and active in version_ids:
            newest = max(versions, key=lambda v: _sortable(v.get("created_at"))).get("id")
            self.checks[f"V7 active_version is the newest {active == newest}"] += 1

        accounted: set[str] = {"artifact.json"}
        for vid in version_ids:
            if not isinstance(vid, str):
                continue
            single = f"versions/{vid}.html"
            listing = f"versions/{vid}.files.json"
            prefix = f"versions/{vid}/"
            directory = [rest for rest in members if rest.startswith(prefix)]
            if single in members:
                self.checks["V3 versions with single .html"] += 1
                accounted.add(single)
                data = zf.read(members[single])
                self._observe_entry_html("V11 single-HTML", data)
            elif listing in members and directory:
                self.checks["V3 versions with .files.json beside a directory"] += 1
                accounted.add(listing)
                accounted.update(directory)
                self._scan_multi_file(zf, members, listing, prefix, documents, errors)
            else:
                self.checks["V3 versions with no files"] += 1
                if listing in members or directory:
                    self.checks["V3 versions with a partial multi-file layout"] += 1
                    accounted.add(listing)
                    accounted.update(directory)

        unaccounted = [rest for rest in members if rest not in accounted]
        kind = descriptor.get("kind")
        root_files = [rest for rest in unaccounted if "/" not in rest]
        other = [rest for rest in unaccounted if "/" in rest]
        if other:
            self.checks["V3 files not attributable to a listed version"] += len(other)
        if kind == "page":
            self.checks["V10 page-kind artifacts"] += 1
            if any(rest.startswith("versions/") for rest in members):
                self.checks["V10 page-kind artifacts with version files"] += 1
        for rest in root_files:
            ext = rest.rpartition(".")[2].lower() if "." in rest else "(none)"
            label = ext if EXTENSION_RE.fullmatch(ext) else "(other)"
            self.censuses[f"V10 root-level files of {census_display(kind)}-kind"][label] += 1
            if ext == "json":
                info = members[rest]
                try:
                    comments = _load_member_json(zf, info)
                except (ValueError, UnicodeDecodeError):
                    self.checks["V10 root-level JSON not parseable"] += 1
                    continue
                _profile_payload(
                    documents,
                    "artifacts/<uuid>/<root-level>.json",
                    comments,
                    info.compress_size,
                    info.file_size,
                )

    def _observe_entry_html(self, label: str, data: bytes) -> None:
        has_doctype, scripts, external = _probe_html(data)
        self.checks[f"{label} entry pages"] += 1
        self.checks[f"{label} entry pages with doctype {has_doctype}"] += 1
        self.checks[f"{label} entry pages with <script> {scripts > 0}"] += 1
        self.checks[f"{label} entry pages referencing external resources {external > 0}"] += 1

    def _scan_multi_file(
        self,
        zf: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        listing: str,
        prefix: str,
        documents: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> None:
        info = members[listing]
        try:
            manifest = _load_member_json(zf, info)
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(f"<version-id>.files.json: not parseable ({type(exc).__name__})")
            return
        _profile_payload(
            documents,
            "artifacts/<uuid>/versions/<version-id>.files.json",
            manifest,
            info.compress_size,
            info.file_size,
        )
        entries = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(entries, list):
            self.checks["V8 .files.json without a files list"] += 1
            return
        listed: set[str] = set()
        html_at_root = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            self.checks["V8 listed files"] += 1
            original = entry.get("original_path")
            exported = entry.get("exported_as")
            mime = entry.get("mime_type")
            self.checks[f"V8 exported_as equals original_path {exported == original}"] += 1
            if not isinstance(exported, str):
                continue
            listed.add(exported)
            depth = exported.count("/")
            self.censuses["V8 listed path depth"][str(depth)] += 1
            if exported.startswith("/") or "\\" in exported or ".." in exported.split("/"):
                self.checks["V8 listed paths absolute, backslashed, or with .."] += 1
            if mime == "text/html" and depth == 0:
                html_at_root += 1
            member = members.get(prefix + exported)
            if member is None:
                self.checks["V8 listed files missing from the ZIP"] += 1
                continue
            data = zf.read(member)
            size_ok = len(data) == entry.get("size_bytes")
            hash_ok = hashlib.sha256(data).hexdigest() == entry.get("sha256")
            self.checks[f"V8 listed files size matches {size_ok}"] += 1
            self.checks[f"V8 listed files sha256 matches {hash_ok}"] += 1
            mime_label = census_display(mime)
            sizes = self.censuses["V9 max bytes by mime_type"]
            sizes[mime_label] = max(sizes[mime_label], len(data))
            if mime == "text/html" and depth == 0:
                self._observe_entry_html("V9 multi-file", data)
            if mime == "application/json":
                try:
                    payload = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    payload = None
                self.checks[
                    f"V13 JSON version files shaped as capability config "
                    f"{_is_capability_config(payload)}"
                ] += 1
        self.censuses["V8 text/html entries at depth 0 per version"][str(html_at_root)] += 1
        present = {rest[len(prefix) :] for rest in members if rest.startswith(prefix)}
        unlisted = present - listed
        if unlisted:
            self.checks["V8 files present but unlisted"] += len(unlisted)

    # -- artifact_data (V12) ----------------------------------------------

    def scan_artifact_data(
        self, zip_path: Path, documents: dict[str, dict[str, Any]], errors: list[str]
    ) -> None:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                match = re.fullmatch(r"artifacts/([^/]+)/data/([^/]+)\.json", info.filename)
                if not match:
                    self.checks["V12 artifact_data members off the data/<name>.json layout"] += 1
                    continue
                self.checks["V12 artifact_data files"] += 1
                self.data_artifact_ids.add(match.group(1))
                try:
                    payload = _load_member_json(zf, info)
                except (ValueError, UnicodeDecodeError) as exc:
                    errors.append(f"artifact data file: not parseable ({type(exc).__name__})")
                    continue
                _profile_payload(
                    documents,
                    "artifacts/<uuid>/data/<masked>.json",
                    payload,
                    info.compress_size,
                    info.file_size,
                    opaque_paths=frozenset({"$.documents[].data"}),
                )
                docs = payload.get("documents") if isinstance(payload, dict) else None
                if isinstance(docs, list):
                    self.checks["V12 data documents"] += len(docs)

    # -- claude_code_cloud (V14–V16) ---------------------------------------

    def scan_claude_code_cloud(
        self, zip_path: Path, documents: dict[str, dict[str, Any]], errors: list[str]
    ) -> None:
        known = {"routines.json", "routine_runs.json"}
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not info.filename.startswith("claude_code_cloud/"):
                    self.checks["V14 members outside claude_code_cloud/"] += 1
                    continue
                leaf = info.filename[len("claude_code_cloud/") :]
                shown = leaf if leaf in known else _mask_segment(leaf, set())
                self.censuses["V14 claude_code_cloud members"][shown] += 1
                if not leaf.endswith(".json"):
                    continue
                try:
                    payload = _load_member_json(zf, info)
                except (ValueError, UnicodeDecodeError) as exc:
                    errors.append(f"claude_code_cloud member: not parseable ({type(exc).__name__})")
                    continue
                _profile_payload(
                    documents,
                    f"claude_code_cloud/{shown}",
                    payload,
                    info.compress_size,
                    info.file_size,
                )
                records = payload.get("claude_code_cloud") if isinstance(payload, dict) else None
                if not isinstance(records, list):
                    self.checks["V14 files without a claude_code_cloud list"] += 1
                    continue
                if leaf == "routines.json":
                    self._observe_routines(records)
                elif leaf == "routine_runs.json":
                    self._observe_runs(records)

    def _observe_routines(self, records: list[Any]) -> None:
        for rec in records:
            if not isinstance(rec, dict):
                continue
            self.checks["V15 routines"] += 1
            rid = rec.get("routine_id")
            if isinstance(rid, str):
                self.routine_ids.add(rid)
                self.checks[f"V15 routine_id has trig_ prefix {rid.startswith('trig_')}"] += 1
            self.checks[f"V15 agent_id is null {rec.get('agent_id') is None}"] += 1
            self.checks[f"V15 ended_at is null {rec.get('ended_at') is None}"] += 1
            schedule = rec.get("schedule") if isinstance(rec.get("schedule"), dict) else {}
            cron = schedule.get("cron") is not None
            once = schedule.get("run_once_at") is not None
            self.checks[f"V15 schedule has exactly one of cron/run_once_at {cron != once}"] += 1
            if cron:
                self.checks["V15 cron schedules"] += 1
            if once:
                self.checks["V15 one-off schedules"] += 1
            if rec.get("prompt_withheld") is True:
                self.checks["V15 prompt_withheld true"] += 1

    def _observe_runs(self, records: list[Any]) -> None:
        for rec in records:
            if not isinstance(rec, dict):
                continue
            self.checks["V16 runs"] += 1
            rid = rec.get("routine_id")
            if isinstance(rid, str):
                self.run_routine_ids.append(rid)
            session = rec.get("session_id")
            prefixed = isinstance(session, str) and session.startswith("cse_")
            self.checks[f"V16 session_id has cse_ prefix {prefixed}"] += 1

    # -- resolution after every file of the export is read ------------------

    def finish(self) -> None:
        if self.data_artifact_ids:
            matched = len(self.data_artifact_ids & self.artifact_ids)
            self.checks["V12 data artifact ids matching a frames artifact"] += matched
            self.checks["V12 data artifact ids with no frames artifact"] += (
                len(self.data_artifact_ids) - matched
            )
        if self.run_routine_ids:
            resolved = sum(1 for rid in self.run_routine_ids if rid in self.routine_ids)
            self.checks["V16 runs whose routine exists"] += resolved
            self.checks["V16 runs whose routine is absent"] += len(self.run_routine_ids) - resolved
        if self.routine_ids:
            with_runs = len(self.routine_ids & set(self.run_routine_ids))
            self.checks["V16 routines with at least one run"] += with_runs
            self.checks["V16 routines with no runs"] += len(self.routine_ids) - with_runs
        if self.artifact_ids:
            linked = (self.linked_ids_structured | self.linked_ids_container) & self.artifact_ids
            self.checks["V17 frames artifacts"] += len(self.artifact_ids)
            self.checks["V17 frames artifacts linked by structured_content or input"] += len(linked)
            self.checks["V17 frames artifact ids found in tool-result text"] += len(
                self.uuids_in_result_text & self.artifact_ids
            )
            self.checks["V17 container.id values naming a frames artifact"] += len(
                self.linked_ids_container & self.artifact_ids
            )
            self.checks[
                "V17 structured_content.artifact_id values naming a frames artifact"
            ] += len(self.linked_ids_structured & self.artifact_ids)
            self.checks[
                "V17 frames artifacts named anywhere in a tool input or structured_content"
            ] += len(self.uuids_in_tool_fields & self.artifact_ids)


# ---------------------------------------------------------------------------
# Export discovery and the scan driver.
# ---------------------------------------------------------------------------

# Categories whose ZIPs carry artifact payloads get member-level handling; the rest are
# profiled as plain JSON. Frames are read first so later categories can resolve ids.
_CATEGORY_ORDER = ("frames", "artifact_data", "claude_code_cloud")


def _category_rank(category: str) -> tuple[int, str]:
    if category in _CATEGORY_ORDER:
        return (_CATEGORY_ORDER.index(category), category)
    return (len(_CATEGORY_ORDER), category)


def _set_inputs(directory: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """``(manifests, category_zips, other_zips)`` directly inside ``directory``."""
    manifests, category_zips, other_zips = [], [], []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix.lower() == ".json":
            manifests.append(entry)
        elif entry.is_file() and entry.suffix.lower() == ".zip":
            (category_zips if CATEGORY_ZIP_RE.match(entry.name) else other_zips).append(entry)
    return manifests, category_zips, other_zips


def discover_exports(target: Path) -> list[tuple[str, list[Path]]]:
    """Split ``target`` into exports: ``[(kind, files)]`` with kind ``legacy`` or ``set``."""
    if target.is_file() and target.suffix.lower() == ".zip":
        if CATEGORY_ZIP_RE.match(target.name):
            return [("set", [target])]
        return [("legacy", [target])]
    if target.is_file() and target.suffix.lower() == ".json":
        return [("set", [target])]
    if not target.is_dir():
        raise SystemExit("unsupported input (expected a .zip, a .json, or a directory)")

    exports: list[tuple[str, list[Path]]] = []
    manifests, category_zips, other_zips = _set_inputs(target)
    exports.extend(("legacy", [z]) for z in other_zips)
    if manifests or category_zips:
        exports.append(("set", manifests + category_zips))
    for sub in sorted(p for p in target.iterdir() if p.is_dir()):
        sub_manifests, sub_category, sub_other = _set_inputs(sub)
        exports.extend(("legacy", [z]) for z in sub_other)
        if sub_manifests or sub_category:
            exports.append(("set", sub_manifests + sub_category))
    if not exports:
        raise SystemExit("no export files found under the given path")
    return exports


def _scan_one(kind: str, files: list[Path]) -> dict[str, Any]:
    errors: list[str] = []
    analysis = ExportAnalysis()
    containers: dict[str, dict[str, dict[str, Any]]] = {}
    inputs: list[str] = []

    def container(name: str) -> dict[str, dict[str, Any]]:
        return containers.setdefault(name, {})

    category_parts: defaultdict[str, list[Path]] = defaultdict(list)
    for path in files:
        if path.suffix.lower() == ".json":
            inputs.append("(manifest)")
            scan_json_file(path, container("(manifest)"), errors, analysis)
        elif kind == "legacy":
            inputs.append("(legacy archive)")
            try:
                scan_zip(path, container("legacy"), errors, analysis)
            except zipfile.BadZipFile:
                errors.append("(legacy archive): not a readable ZIP")
        else:
            match = CATEGORY_ZIP_RE.match(path.name)
            category = match.group("category") if match else "(unnamed)"
            category_parts[category].append(path)

    for category in sorted(category_parts, key=_category_rank):
        parts = sorted(category_parts[category])
        for part in parts:
            inputs.append(mask_uuids(part.name))
            documents = container(category)
            try:
                if category == "frames":
                    analysis.scan_frames(part, documents, errors)
                elif category == "artifact_data":
                    analysis.scan_artifact_data(part, documents, errors)
                elif category == "claude_code_cloud":
                    analysis.scan_claude_code_cloud(part, documents, errors)
                else:
                    scan_zip(part, documents, errors, analysis)
            except zipfile.BadZipFile:
                errors.append(f"{mask_uuids(part.name)}: not a readable ZIP")
        if len(parts) > 1:
            errors.append(f"category {category}: {len(parts)} parts merged")
    analysis.finish()

    report_containers: dict[str, Any] = {}
    for name, documents in containers.items():
        if not documents:
            continue
        report_containers[name] = {
            display: {
                "files": group["files"],
                "compressed_bytes": group["compressed_bytes"],
                "uncompressed_bytes": group["uncompressed_bytes"],
                "schema": group["profiler"].summary(),
            }
            for display, group in sorted(documents.items())
        }
    return {
        "kind": kind,
        "inputs": inputs,
        "categories": sorted(category_parts, key=_category_rank),
        "containers": report_containers,
        "checks": dict(sorted(analysis.checks.items())),
        "censuses": {name: dict(c.most_common()) for name, c in sorted(analysis.censuses.items())},
        "errors": errors,
        "_digests": (analysis.conversations_memory_digest, analysis.memory_files_digest),
    }


def _apply_cardinality_cap(exports: list[dict[str, Any]]) -> None:
    """Suppress value censuses for fields with more than ENUM_CARDINALITY_CAP distinct
    values across the whole scan. A field is one schema path within one document
    group (or one named check census), pooled over every export scanned."""
    distinct: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for export in exports:
        for documents in export["containers"].values():
            for display, doc in documents.items():
                for path, stats in doc["schema"].items():
                    distinct[(display, path)].update(stats.get("value_census", {}))
        for name, census in export["censuses"].items():
            if not name.startswith("V9 max bytes"):
                distinct[("(checks)", name)].update(census)
    capped = {field for field, values in distinct.items() if len(values) > ENUM_CARDINALITY_CAP}
    for export in exports:
        for documents in export["containers"].values():
            for display, doc in documents.items():
                for path, stats in doc["schema"].items():
                    if (display, path) in capped and "value_census" in stats:
                        stats.pop("value_census")
                        stats["value_census_suppressed"] = len(distinct[(display, path)])
        for name in list(export["censuses"]):
            if ("(checks)", name) in capped:
                export["censuses"][name] = {
                    f"<suppressed: {len(distinct[('(checks)', name)])} distinct>": 0
                }


def scan(target: Path) -> dict[str, Any]:
    """Scan one export or a directory of several, returning an aggregate-only report."""
    exports = [_scan_one(kind, files) for kind, files in discover_exports(target)]
    _apply_cardinality_cap(exports)
    cross: list[str] = []
    digests = [export.pop("_digests") for export in exports]
    for i in range(len(exports)):
        for j in range(i + 1, len(exports)):
            for slot, label in ((0, "conversations_memory"), (1, "memory_files")):
                a, b = digests[i][slot], digests[j][slot]
                if a is not None and b is not None:
                    verdict = "identical" if a == b else "different"
                    cross.append(f"V20 export {i + 1} vs export {j + 1}: {label} {verdict}")
    return {"exports": exports, "cross_export": cross}


def _format_schema(doc: dict[str, Any], lines: list[str]) -> None:
    for path, stats in doc["schema"].items():
        type_bits = ", ".join(f"{t}:{n}" for t, n in sorted(stats["types"].items()))
        lines.append(f"  {path}  [{type_bits}]")
        if "dict_keys" in stats:
            for key, presence in stats["dict_keys"].items():
                if presence["present"] > presence["of"]:
                    lines.append(
                        f"      .{key}: {presence['present']} keys across "
                        f"{presence['of']} dict(s) (masked aggregate)"
                    )
                else:
                    ratio = presence["present"] / presence["of"] * 100
                    lines.append(
                        f"      .{key}: {presence['present']}/{presence['of']} ({ratio:.0f}%)"
                    )
        if "str_len" in stats:
            s = stats["str_len"]
            extras = "".join(
                f"  {label}={s[label]}"
                for label in ("distinct", "empty", "frontmatter", "uuid_shaped", "timestamp_shaped")
                if label in s
            )
            lines.append(f"      str len min/max/avg: {s['min']}/{s['max']}/{s['avg']}{extras}")
        if "list_len" in stats:
            s = stats["list_len"]
            empty = f"  empty={s['empty']}" if "empty" in s else ""
            lines.append(f"      list/key len min/max/avg: {s['min']}/{s['max']}/{s['avg']}{empty}")
        if "bool_values" in stats:
            bits = ", ".join(f"{k}:{v}" for k, v in sorted(stats["bool_values"].items()))
            lines.append(f"      bool values: {bits}")
        if "value_census" in stats:
            bits = ", ".join(f"{k}:{v}" for k, v in stats["value_census"].items())
            lines.append(f"      value census: {bits}")
        if "value_census_suppressed" in stats:
            lines.append(
                f"      value census suppressed: {stats['value_census_suppressed']} distinct "
                f"(cap {ENUM_CARDINALITY_CAP})"
            )
        if "top_segment_census" in stats:
            bits = ", ".join(f"{k}:{v}" for k, v in stats["top_segment_census"].items())
            lines.append(f"      top-level segment census: {bits}")


def format_report(report: dict[str, Any], *, checks_only: bool = False) -> str:
    lines: list[str] = []
    for number, export in enumerate(report["exports"], start=1):
        lines.append(f"#### export {number}: {export['kind']}")
        lines.append(f"  inputs: {', '.join(export['inputs'])}")
        if export["categories"]:
            lines.append(f"  categories: {', '.join(export['categories'])}")
        lines.append("  checks:")
        for name, count in export["checks"].items():
            lines.append(f"    {name}: {count}")
        for name, census in export["censuses"].items():
            bits = ", ".join(f"{k}:{v}" for k, v in census.items())
            lines.append(f"    {name}: {bits}")
        if not checks_only:
            for container_name, documents in export["containers"].items():
                for display, doc in documents.items():
                    lines.append("")
                    lines.append(f"== {container_name} :: {display} ==")
                    lines.append(
                        f"  files: {doc['files']}  compressed: {doc['compressed_bytes']:,} B"
                        f"  uncompressed: {doc['uncompressed_bytes']:,} B"
                    )
                    _format_schema(doc, lines)
        if export["errors"]:
            lines.append("  notes:")
            lines.extend(f"    {err}" for err in export["errors"])
        lines.append("")
    if report["cross_export"]:
        lines.append("#### across exports")
        lines.extend(f"  {line}" for line in report["cross_export"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the JSON schema of Claude.ai data exports (legacy single ZIP, a "
            "current-format manifest + category zips, or a directory holding several). "
            "Output is aggregate-only: no conversation content, names, or identifiers "
            "are printed."
        )
    )
    parser.add_argument(
        "path",
        help="legacy export ZIP, manifest .json, export-set directory, or a directory of exports",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--checks-only", action="store_true", help="print the derived checks without schemas"
    )
    args = parser.parse_args(argv)

    report = scan(Path(args.path))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report, checks_only=args.checks_only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
