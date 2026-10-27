"""In-process inspect mode (v1.2 Session K).

An inspected export is a temporary, read-only view over an archive that
isn't the stored one — a test corpus, a second account's archive. The
no-trace rule: inspection never writes to disk. The uploaded bytes are
parsed straight from memory (:func:`viewer.parser.load_export_from_bytes`),
the search index lives in an in-memory SQLite database, no parse pickle is
written, and no ``upload_history`` row is recorded. Re-inspecting the same
file re-parses; that cost is accepted for a one-off mode.

The primary ``Export`` stays resident in ``app.config["EXPORT"]`` for the
whole inspection, so returning to it is instant and needs no reload — an
app restart likewise always comes back on the primary export, because this
state is in-process memory only.

Single-process dev server: the state is app-global, so concurrent browser
tabs share the one inspected view. Accepted for a single-user localhost
tool.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from viewer.search import index_export

if TYPE_CHECKING:
    from flask import Flask

    from viewer.parser import Export

_CONFIG_KEY = "INSPECT_STATE"


@dataclass
class InspectState:
    """Everything inspect mode holds: the parsed export, the display
    filename for the banner, and the in-memory search index.

    ``search_conn`` is created with ``check_same_thread=False`` because the
    threaded dev server handles later page requests on different threads
    from the POST that built the index; access after the build is read-only,
    and concurrent tabs sharing one view is accepted for this tool.
    """

    export: Export
    filename: str
    search_conn: sqlite3.Connection
    # Expected-vs-supplied counts when the inspected set carried a manifest
    # (record §4's degraded-view banner) — ``None`` otherwise.
    manifest_status: tuple[int, int] | None = None
    # The inspected manifest's distinct categories — the A7 banner's
    # expected set (export-categories record §4); ``()`` without a manifest.
    manifest_categories: tuple[str, ...] = ()
    # The inspected ``frames`` ZIPs' bytes, by filename: artifact files are
    # read on demand from here (export-categories record §6.1) — memory only,
    # so the no-trace rule holds.
    part_blobs: dict[str, bytes] = field(default_factory=dict)


def begin_inspection(
    app: Flask,
    export: Export,
    filename: str,
    *,
    manifest_status: tuple[int, int] | None = None,
    manifest_categories: tuple[str, ...] = (),
    part_blobs: dict[str, bytes] | None = None,
) -> InspectState:
    """Enter inspect mode (replacing any inspection already in progress).

    Builds the in-memory FTS index before touching app state, so a failure
    leaves the current view — primary or previously inspected — intact.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    index_export(conn, export)
    end_inspection(app)
    state = InspectState(
        export=export,
        filename=filename,
        search_conn=conn,
        manifest_status=manifest_status,
        manifest_categories=manifest_categories,
        part_blobs=dict(part_blobs or {}),
    )
    app.config[_CONFIG_KEY] = state
    return state


def end_inspection(app: Flask) -> None:
    """Leave inspect mode, discarding the in-memory index. No-op when not
    inspecting."""
    state = app.config.pop(_CONFIG_KEY, None)
    if state is not None:
        state.search_conn.close()


def get_inspection(app: Flask) -> InspectState | None:
    """The current :class:`InspectState`, or ``None`` when not inspecting."""
    return app.config.get(_CONFIG_KEY)
