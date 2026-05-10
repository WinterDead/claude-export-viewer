"""SQLite layer for the manual conversation→project tagging feature (plan §12 step 4).

Pure functions taking a ``sqlite3.Connection`` — no Flask coupling, so the
module can be unit-tested against an in-memory database.

Schema:

    conversation_project (
        conversation_uuid TEXT PRIMARY KEY,
        project_uuid      TEXT NOT NULL,
        tagged_at         TEXT NOT NULL    -- ISO 8601 UTC
    )

Untagged conversations simply have no row. Re-tagging upserts.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_project (
    conversation_uuid TEXT PRIMARY KEY,
    project_uuid      TEXT NOT NULL,
    tagged_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_project_project
    ON conversation_project (project_uuid);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create the tagging schema if it doesn't exist. Idempotent."""
    conn.executescript(_SCHEMA)
    conn.commit()


def set_tag(conn: sqlite3.Connection, conversation_uuid: str, project_uuid: str | None) -> None:
    """Tag a conversation to a project, or clear its tag (``project_uuid=None``)."""
    if project_uuid is None:
        conn.execute(
            "DELETE FROM conversation_project WHERE conversation_uuid = ?",
            (conversation_uuid,),
        )
    else:
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO conversation_project (conversation_uuid, project_uuid, tagged_at)
            VALUES (?, ?, ?)
            ON CONFLICT(conversation_uuid) DO UPDATE SET
                project_uuid = excluded.project_uuid,
                tagged_at    = excluded.tagged_at
            """,
            (conversation_uuid, project_uuid, now),
        )
    conn.commit()


def get_tag(conn: sqlite3.Connection, conversation_uuid: str) -> str | None:
    """Return the project_uuid this conversation is tagged to, or ``None``."""
    row = conn.execute(
        "SELECT project_uuid FROM conversation_project WHERE conversation_uuid = ?",
        (conversation_uuid,),
    ).fetchone()
    return row[0] if row else None


def all_tags(conn: sqlite3.Connection) -> dict[str, str]:
    """Return ``{conversation_uuid: project_uuid}`` for every tagged conversation.

    One query is cheaper than N ``get_tag`` calls when rendering a list.
    """
    rows = conn.execute(
        "SELECT conversation_uuid, project_uuid FROM conversation_project"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def tags_for_project(conn: sqlite3.Connection, project_uuid: str) -> list[str]:
    """Return the conversation_uuids tagged to a given project."""
    rows = conn.execute(
        "SELECT conversation_uuid FROM conversation_project WHERE project_uuid = ?",
        (project_uuid,),
    ).fetchall()
    return [r[0] for r in rows]
