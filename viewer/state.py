"""Tag-state resolution against the in-memory ``Export`` (plan §12 step 7).

Tags persist in SQLite by ``conversation_uuid`` → ``project_uuid``. On
re-import, two preserve-and-flag categories surface:

- **Orphan conversation tags** — rows whose ``conversation_uuid`` isn't in
  ``Export.conversations``. The conversation was deleted from Claude.ai (or
  isn't in this batch) but the user's manual tagging shouldn't be silently
  discarded.
- **Orphan project tags** — rows whose ``project_uuid`` isn't in
  ``Export.projects``. The conversation still exists; the project it was
  tagged to no longer does. The conv-detail dropdown surfaces this case so
  the user can retag or clear.

This module computes the resolution; routes consume the resulting
:class:`TagState` rather than the raw ``all_tags()`` dict so templates only
ever render resolved data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from viewer.tagging import all_tags

if TYPE_CHECKING:
    from viewer.parser import Export


@dataclass(frozen=True, slots=True)
class TagState:
    """Resolution of every row in the ``conversation_project`` table against
    the current ``Export``.

    All three dicts share the convention ``{conversation_uuid: project_uuid}``
    so call sites can iterate over a single shape. ``orphan_count`` is the
    sum of orphan rows across both kinds — what the banner displays.
    """

    live: dict[str, str]
    orphan_conv: dict[str, str]
    orphan_project: dict[str, str]

    @property
    def orphan_count(self) -> int:
        return len(self.orphan_conv) + len(self.orphan_project)

    @property
    def orphan_conv_uuids(self) -> list[str]:
        return sorted(self.orphan_conv)

    @property
    def orphan_project_uuids(self) -> list[str]:
        """Distinct project_uuids that appear as orphan-project tags."""
        return sorted(set(self.orphan_project.values()))


def compute_tag_state(conn: sqlite3.Connection, export: Export) -> TagState:
    """Read every tag row and bucket it into live/orphan-conv/orphan-project."""
    raw = all_tags(conn)
    conv_uuids = {c.uuid for c in export.conversations}
    project_uuids = {p.uuid for p in export.projects}

    live: dict[str, str] = {}
    orphan_conv: dict[str, str] = {}
    orphan_project: dict[str, str] = {}

    for cuid, puid in raw.items():
        if cuid not in conv_uuids:
            orphan_conv[cuid] = puid
        elif puid not in project_uuids:
            orphan_project[cuid] = puid
        else:
            live[cuid] = puid

    return TagState(live=live, orphan_conv=orphan_conv, orphan_project=orphan_project)


def purge_orphan_tags(conn: sqlite3.Connection, state: TagState) -> int:
    """Delete every row in ``conversation_project`` that ``state`` classifies
    as orphan (either kind). Returns the deleted row count.

    Caller MUST gate this behind an explicit confirmation step — the function
    itself does no checking. See ``viewer.routes.orphan_purge`` for the
    request-level guard (CSRF + a "yes, delete N rows" checkbox).
    """
    targets = list(state.orphan_conv) + list(state.orphan_project)
    if not targets:
        return 0
    placeholders = ",".join("?" for _ in targets)
    cur = conn.execute(
        f"DELETE FROM conversation_project WHERE conversation_uuid IN ({placeholders})",
        targets,
    )
    conn.commit()
    return cur.rowcount or 0
