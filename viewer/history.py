"""SQLite layer for the upload-history log (v1.1 Session A, item A3).

Pure functions taking a ``sqlite3.Connection`` — same shape as
:mod:`viewer.tagging`, so the module can be unit-tested against an
in-memory database. Lives in the existing ``cache/viewer.sqlite``.

Schema:

    upload_history (
        id               INTEGER PRIMARY KEY,
        recorded_at_utc  TEXT NOT NULL,   -- ISO 8601 UTC
        source           TEXT NOT NULL,   -- 'upload' or 'startup'
        filename         TEXT NOT NULL,
        zip_sha256       TEXT NOT NULL,   -- same hash that keys the pickle cache
        conversations    INTEGER NOT NULL,
        messages         INTEGER NOT NULL,
        projects         INTEGER NOT NULL,
        project_memories INTEGER NOT NULL
    )

A row is recorded only after a successful parse. Uploads always record — an
explicit user action is worth logging even when the sha matches an earlier
row. Startup loads record only when the sha differs from the most recent
row, so an app relaunch doesn't duplicate the last entry; synthetic-fixture
loads record nothing (the caller never invokes this module for them).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from viewer.parser import Export

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_history (
    id               INTEGER PRIMARY KEY,
    recorded_at_utc  TEXT NOT NULL,
    source           TEXT NOT NULL,
    filename         TEXT NOT NULL,
    zip_sha256       TEXT NOT NULL,
    conversations    INTEGER NOT NULL,
    messages         INTEGER NOT NULL,
    projects         INTEGER NOT NULL,
    project_memories INTEGER NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One recorded load. ``recorded_at`` is the parsed timestamp; ``None``
    when ``recorded_at_utc`` doesn't parse (preserve-and-flag — the raw
    string is kept in ``recorded_at_raw`` so the renderer can still show it)."""

    id: int
    recorded_at: datetime | None
    recorded_at_raw: str
    source: str
    filename: str
    zip_sha256: str
    conversations: int
    messages: int
    projects: int
    project_memories: int


def init_db(conn: sqlite3.Connection) -> None:
    """Create the upload-history schema if it doesn't exist. Idempotent."""
    conn.executescript(_SCHEMA)
    conn.commit()


def export_counts(export: Export) -> tuple[int, int, int, int]:
    """The four counts a history row stores, taken from a parsed export.

    ``project_memories`` counts memories attached to a resolvable project —
    the same definition as the home-page stat. Orphan memories are tracked
    separately by the parser and aren't part of this count.
    """
    return (
        len(export.conversations),
        sum(len(c.messages) for c in export.conversations),
        len(export.projects),
        sum(1 for p in export.projects if p.memory is not None),
    )


def record_load(
    conn: sqlite3.Connection,
    *,
    source: str,
    filename: str,
    zip_sha256: str,
    export: Export,
) -> None:
    """Insert a history row for a successfully parsed export.

    The caller decides whether the load is worth recording — uploads always
    are; startup loads go through :func:`record_startup_load` instead so the
    duplicate-sha check happens in one place.
    """
    convs, msgs, projs, mems = export_counts(export)
    conn.execute(
        """
        INSERT INTO upload_history (
            recorded_at_utc, source, filename, zip_sha256,
            conversations, messages, projects, project_memories
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (datetime.now(UTC).isoformat(), source, filename, zip_sha256, convs, msgs, projs, mems),
    )
    conn.commit()


def latest_sha(conn: sqlite3.Connection) -> str | None:
    """The ``zip_sha256`` of the most recent history row, or ``None``."""
    row = conn.execute("SELECT zip_sha256 FROM upload_history ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def record_startup_load(
    conn: sqlite3.Connection,
    *,
    filename: str,
    zip_sha256: str,
    export: Export,
) -> bool:
    """Record a startup load unless the most recent row already has this sha.

    Returns ``True`` when a row was recorded. The duplicate-sha check only
    looks at the latest row — relaunching the app with an unchanged archive
    adds nothing, while a changed archive (or an upload in between) does.
    """
    if latest_sha(conn) == zip_sha256:
        return False
    record_load(conn, source="startup", filename=filename, zip_sha256=zip_sha256, export=export)
    return True


def all_history(conn: sqlite3.Connection) -> list[HistoryRow]:
    """Every history row, most recent first."""
    rows = conn.execute("""
        SELECT id, recorded_at_utc, source, filename, zip_sha256,
               conversations, messages, projects, project_memories
          FROM upload_history
         ORDER BY id DESC
        """).fetchall()
    return [_to_row(r) for r in rows]


def _to_row(r: sqlite3.Row | tuple) -> HistoryRow:
    raw_ts = r[1]
    try:
        recorded_at: datetime | None = datetime.fromisoformat(raw_ts)
    except (TypeError, ValueError):
        # Preserve-and-flag: keep the raw string for display, don't drop the row.
        logger.warning("upload history: unparseable recorded_at_utc %r on row %s", raw_ts, r[0])
        recorded_at = None
    return HistoryRow(
        id=r[0],
        recorded_at=recorded_at,
        recorded_at_raw=raw_ts if isinstance(raw_ts, str) else str(raw_ts),
        source=r[2],
        filename=r[3],
        zip_sha256=r[4],
        conversations=r[5],
        messages=r[6],
        projects=r[7],
        project_memories=r[8],
    )
