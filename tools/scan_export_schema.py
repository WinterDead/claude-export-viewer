"""Profile the JSON schema of a Claude.ai data export, emitting aggregates only.

Accepts either container format:

- a legacy single-zip export (``data-<...>-batch-0000.zip`` or any ZIP holding
  ``conversations.json`` etc.) — pass the ZIP path directly;
- a new-format export set — pass a directory holding the manifest ``.json`` and the
  category zips (``light_metadata-000.zip``, ``projects-000.zip``, ``memories-000.zip``,
  ``conversations-000.zip``); any subset may be present. A bare manifest ``.json`` path
  also works.

Multi-part category files (``conversations-001.zip``, …) are grouped and scanned as one
category. This is structural tolerance only: no multi-part archive has been observed
(every sampled export carries only part ``000``), so assembly across parts is untested
against real data.

For every JSON document the scanner reports a schema profile: each path, its observed
value types with counts, optional-key presence ratios for dicts, and min/max/avg lengths
for strings and lists. Content never appears in the output — privacy is by construction,
not operator discipline:

- Verbatim values are printed only for the allowlisted machine-enum fields in
  ``ENUM_FIELDS``, and only when the value passes the machine-enum shape check
  (short single token, no whitespace, no sentence punctuation). Anything that fails
  the check prints as a masked placeholder carrying only its length.
- UUID-shaped strings are masked everywhere: enum values, dictionary keys used as path
  segments, ZIP member names, and input filenames.
- Dictionary keys that do not look like machine identifiers are masked in paths.
- ``memory_files[].path`` values get a census of their top-level segment only (the
  directory or well-known filename before the first ``/``), shape-checked like enum
  values — slugs below the top level are never printed. No other path-like field gets
  a value census.
- All other string handling is length/count statistics only (including counts of empty
  values and of strings opening with a ``---`` frontmatter fence).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
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

    def __init__(self) -> None:
        self.paths: defaultdict[str, _PathStats] = defaultdict(_PathStats)

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
        elif isinstance(value, dict):
            stats.types["dict"] += 1
            stats.dict_count += 1
            for child_key, child_value in value.items():
                segment = path_segment(child_key)
                stats.key_presence[segment] += 1
                self.walk(child_value, f"{path}.{segment}", child_key)
        else:  # pragma: no cover - json.load never yields other types
            stats.types[type(value).__name__] += 1

        if key in ENUM_FIELDS and not isinstance(value, dict | list):
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


def scan_zip(zip_path: Path, documents: dict[str, dict[str, Any]], errors: list[str]) -> None:
    """Profile every JSON member of one ZIP into the shared document-group map."""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".json"):
                continue
            display = _member_display(info.filename)
            group = documents.setdefault(
                display,
                {
                    "files": 0,
                    "compressed_bytes": 0,
                    "uncompressed_bytes": 0,
                    "profiler": SchemaProfiler(),
                },
            )
            group["files"] += 1
            group["compressed_bytes"] += info.compress_size
            group["uncompressed_bytes"] += info.file_size
            try:
                with zf.open(info) as fh:
                    payload = json.load(fh)
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(
                    f"{mask_uuids(zip_path.name)}:{display}: "
                    f"not parseable JSON ({type(exc).__name__})"
                )
                continue
            group["profiler"].walk(payload)


def scan_json_file(
    json_path: Path, documents: dict[str, dict[str, Any]], errors: list[str]
) -> None:
    display = mask_uuids(json_path.name)
    group = documents.setdefault(
        display,
        {"files": 0, "compressed_bytes": 0, "uncompressed_bytes": 0, "profiler": SchemaProfiler()},
    )
    group["files"] += 1
    size = json_path.stat().st_size
    group["compressed_bytes"] += size
    group["uncompressed_bytes"] += size
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        errors.append(f"{display}: not parseable JSON ({type(exc).__name__})")
        return
    group["profiler"].walk(payload)


def scan(target: Path) -> dict[str, Any]:
    """Scan a legacy ZIP, a manifest JSON, or a new-format export directory."""
    errors: list[str] = []
    inputs: list[str] = []
    # container name → {display member name → group}
    containers: dict[str, dict[str, dict[str, Any]]] = {}

    def container(name: str) -> dict[str, dict[str, Any]]:
        return containers.setdefault(name, {})

    if target.is_file() and target.suffix.lower() == ".zip":
        inputs.append(mask_uuids(target.name))
        scan_zip(target, container(mask_uuids(target.name)), errors)
    elif target.is_file() and target.suffix.lower() == ".json":
        inputs.append(mask_uuids(target.name))
        scan_json_file(target, container("(manifest)"), errors)
    elif target.is_dir():
        category_parts: defaultdict[str, list[Path]] = defaultdict(list)
        for entry in sorted(target.iterdir()):
            if entry.is_file() and entry.suffix.lower() == ".json":
                inputs.append(mask_uuids(entry.name))
                scan_json_file(entry, container("(manifest)"), errors)
            elif entry.is_file() and entry.suffix.lower() == ".zip":
                match = CATEGORY_ZIP_RE.match(entry.name)
                if match:
                    category_parts[match.group("category")].append(entry)
                else:
                    # A non-category ZIP in the directory is treated as a standalone
                    # (legacy-format) export archive.
                    inputs.append(mask_uuids(entry.name))
                    scan_zip(entry, container(mask_uuids(entry.name)), errors)
        for category, parts in sorted(category_parts.items()):
            for part_path in sorted(parts):
                inputs.append(mask_uuids(part_path.name))
                scan_zip(part_path, container(category), errors)
            if len(parts) > 1:
                errors.append(
                    f"category {category}: {len(parts)} parts merged; "
                    "multi-part assembly is untested against real data"
                )
    else:
        raise SystemExit(f"unsupported input: {target} (expected a .zip, a .json, or a directory)")

    if not inputs:
        raise SystemExit(f"no export files found under {target}")

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
    return {"inputs": inputs, "containers": report_containers, "errors": errors}


def format_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("inputs:")
    for name in report["inputs"]:
        lines.append(f"  {name}")
    for container_name, documents in report["containers"].items():
        for display, doc in documents.items():
            lines.append("")
            lines.append(f"== {container_name} :: {display} ==")
            lines.append(
                f"  files: {doc['files']}  compressed: {doc['compressed_bytes']:,} B"
                f"  uncompressed: {doc['uncompressed_bytes']:,} B"
            )
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
                                f"      .{key}: {presence['present']}/{presence['of']} "
                                f"({ratio:.0f}%)"
                            )
                if "str_len" in stats:
                    s = stats["str_len"]
                    extras = "".join(
                        f"  {label}={s[label]}"
                        for label in (
                            "distinct",
                            "empty",
                            "frontmatter",
                            "uuid_shaped",
                            "timestamp_shaped",
                        )
                        if label in s
                    )
                    lines.append(
                        f"      str len min/max/avg: {s['min']}/{s['max']}/{s['avg']}{extras}"
                    )
                if "list_len" in stats:
                    s = stats["list_len"]
                    empty = f"  empty={s['empty']}" if "empty" in s else ""
                    lines.append(
                        f"      list len min/max/avg: {s['min']}/{s['max']}/{s['avg']}{empty}"
                    )
                if "bool_values" in stats:
                    bits = ", ".join(f"{k}:{v}" for k, v in sorted(stats["bool_values"].items()))
                    lines.append(f"      bool values: {bits}")
                if "value_census" in stats:
                    bits = ", ".join(f"{k}:{v}" for k, v in stats["value_census"].items())
                    lines.append(f"      value census: {bits}")
                if "top_segment_census" in stats:
                    bits = ", ".join(f"{k}:{v}" for k, v in stats["top_segment_census"].items())
                    lines.append(f"      top-level segment census: {bits}")
    if report["errors"]:
        lines.append("")
        lines.append("notes:")
        for err in report["errors"]:
            lines.append(f"  {err}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the JSON schema of a Claude.ai data export (legacy single ZIP or "
            "new-format manifest + category zips). Output is aggregate-only: no "
            "conversation content, names, or identifiers are printed."
        )
    )
    parser.add_argument(
        "path",
        help="legacy export ZIP, manifest .json, or directory holding a new-format export set",
    )
    parser.add_argument("--json", action="store_true", help="emit the profile as JSON")
    args = parser.parse_args(argv)

    report = scan(Path(args.path))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
