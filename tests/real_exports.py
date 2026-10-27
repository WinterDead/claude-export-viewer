"""Discovery and invariants for the opt-in real-data tests (v1.3.1).

Real exports never live in the project tree. The real-data tests read the directory named
by ``VIEWER_REAL_EXPORT_DIR`` and skip when it is unset or missing. The directory may hold
several exports: legacy single ZIPs at its root, and current-format sets in its immediate
subdirectories. Each recognised export becomes one parametrised case, identified by kind
and position only — directory and file names are never put in a test id or a message.

The assertions are **invariants, not counts** (counts belong to one export and go stale),
and every failure message carries aggregate counts only.
"""

from __future__ import annotations

import os
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from viewer.categories import LAYOUT_MULTI_FILE, orphan_runs
from viewer.parser import (
    CONTAINER_LEGACY,
    KNOWN_BLOCK_TYPES,
    Export,
    UnknownBlock,
    load_export,
    sniff_container,
)
from viewer.search import _expected_row_count, index_export

REAL_EXPORT_ENV = "VIEWER_REAL_EXPORT_DIR"


@dataclass(frozen=True)
class RealExports:
    legacy: list[Path]
    sets: list[Path]
    skip_reason: str | None


def _is_legacy_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return sniff_container(set(zf.namelist())) == CONTAINER_LEGACY
    except (zipfile.BadZipFile, OSError):
        return False


def discover(environ: dict[str, str] | None = None) -> RealExports:
    """Find the exports under ``VIEWER_REAL_EXPORT_DIR`` (content decides, not names)."""
    env = os.environ if environ is None else environ
    raw = env.get(REAL_EXPORT_ENV, "").strip()
    if not raw:
        return RealExports([], [], f"{REAL_EXPORT_ENV} is unset")
    root = Path(raw)
    if not root.is_dir():
        return RealExports([], [], f"{REAL_EXPORT_ENV} does not name an existing directory")
    legacy = [p for p in sorted(root.glob("*.zip")) if _is_legacy_zip(p)]
    sets = [d for d in sorted(p for p in root.iterdir() if p.is_dir()) if any(d.glob("*.zip"))]
    reason = None if legacy or sets else f"{REAL_EXPORT_ENV} holds no recognisable export"
    return RealExports(legacy, sets, reason)


def params(kind: str) -> list:
    """``pytest.param`` cases for one kind (``legacy`` or ``set``), or one skipped case
    whose reason names the variable."""
    found = discover()
    paths = found.legacy if kind == "legacy" else found.sets
    if not paths:
        reason = found.skip_reason or f"{REAL_EXPORT_ENV} holds no {kind} export"
        return [pytest.param(None, marks=pytest.mark.skip(reason=reason), id=f"{kind}-none")]
    return [pytest.param(p, id=f"{kind}-{i}") for i, p in enumerate(paths, start=1)]


def load_quietly(path: Path) -> Export:
    """Load a real export; on failure, fail the test naming only the exception type.

    The failure is raised outside the ``except`` block so pytest never chains (and
    prints) the original message, which may name identifiers."""
    error: str | None = None
    try:
        return load_export(path)
    except Exception as exc:  # noqa: BLE001 - report the type only, never the message
        error = type(exc).__name__
    pytest.fail(f"real export raised {error}", pytrace=False)


def unknown_known_types(export: Export) -> int:
    """Blocks preserved as UnknownBlock although the parser types their kind."""
    return sum(
        1
        for conv in export.conversations
        for msg in conv.messages
        for block in msg.content
        if isinstance(block, UnknownBlock) and block.type in KNOWN_BLOCK_TYPES
    )


def unresolved_parent_graphs(export: Export) -> int:
    """Conversations with an orphan parent or a message unreachable from any root."""
    bad = 0
    for conv in export.conversations:
        uuids = {m.uuid for m in conv.messages}
        orphan = any(
            m.parent_uuid is not None and m.parent_uuid not in uuids for m in conv.messages
        )
        roots = [m.uuid for m in conv.messages if m.parent_uuid is None]
        reached: set[str] = set()
        stack = list(roots)
        while stack:
            node = stack.pop()
            if node in reached:
                continue
            reached.add(node)
            stack.extend(conv.children_by_uuid.get(node, []))
        if orphan or reached != uuids:
            bad += 1
    return bad


def later_category_problems(export: Export) -> dict[str, int]:
    """v1.4.0 invariants (export-categories record §6), as counts of violations."""
    unresolved_active = sum(1 for a in export.artifacts if "active_version_missing" in a.flags)
    failed_integrity = sum(
        1
        for a in export.artifacts
        for v in a.versions
        if v.layout == LAYOUT_MULTI_FILE and (v.unlisted_files or not all(f.ok for f in v.files))
    )
    known_tasks = {t.routine_id for t in export.scheduled_tasks}
    resolved = sum(1 for r in export.task_runs if r.routine_id in known_tasks)
    orphans = len(orphan_runs(export.scheduled_tasks, export.task_runs))
    return {
        "unresolved active versions": unresolved_active,
        "multi-file versions failing integrity": failed_integrity,
        "runs neither resolved nor preserved": len(export.task_runs) - resolved - orphans,
    }


def assert_invariants(export: Export) -> None:
    """The real-data invariants; failure messages are aggregate counts only."""
    unknown = unknown_known_types(export)
    assert unknown == 0, f"{unknown} blocks of a known type were preserved as unknown"
    unresolved = unresolved_parent_graphs(export)
    assert unresolved == 0, (
        f"{unresolved} of {len(export.conversations)} conversations have an unresolved "
        "parent graph"
    )
    conn = sqlite3.connect(":memory:")
    drifted = False
    try:
        rows = index_export(conn, export)
    except RuntimeError:
        drifted = True
    finally:
        conn.close()
    if drifted:
        pytest.fail("search index row count drifted from its expected formula", pytrace=False)
    expected = _expected_row_count(export)
    assert rows == expected, f"search index holds {rows} rows, expected {expected}"
    problems = {k: v for k, v in later_category_problems(export).items() if v}
    assert not problems, f"later-category invariants violated: {problems}"
