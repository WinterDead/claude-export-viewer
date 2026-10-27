# Proposal — portable, mergeable tag state

**Status: proposal for chat ratification. Nothing here is ratified.** Carried in the plan's
1.5+ list (formerly 1.4+): "The tag database is the app's only irreplaceable state; the
export is re-downloadable, the triage work is not."

## Problem

Tags live in one SQLite file on one machine. A user who triages on a laptop and a desktop,
or reinstalls, has no supported way to carry the work across, and no way to combine two
machines' work. Backups (Help, "Backups and your data") restore a whole file, which
overwrites rather than merges.

## Evidence from the code

- `viewer/tagging.py`: one table, `conversation_project(conversation_uuid PRIMARY KEY,
  project_uuid, tagged_at)`; re-tagging upserts; clearing deletes the row. Untagged = no
  row, so a cleared tag leaves no trace — deletions can't merge.
- The No-Project sentinel `__no_project__` is stored like any project id.
- `viewer/state.py` classifies rows as live / orphan-conversation / orphan-project against
  the loaded export; purging orphans is an explicit user action.
- Tags reference export UUIDs only — no machine or account identity is stored.

## Options

| Option | Merge semantics | Trade-offs |
|---|---|---|
| A. Export/import a JSON tag file, last-write-wins by `tagged_at` | Union of rows; on conflict the newer `tagged_at` wins | Simple, inspectable file; deletions don't propagate (a cleared tag on one machine is resurrected by the other's row) unless tombstones are added. |
| B. A + tombstones | Clearing writes a tombstone row (`project_uuid = NULL`, `cleared_at`); merge compares the latest event per conversation | Deletions merge correctly; needs a schema migration and a rule for very old tombstones. |
| C. A + surfaced conflicts | Rows that differ and were both changed since the last import are listed for the user to resolve | Most honest; needs a "last import" marker per source and a review page. |
| D. Shared database file via a sync folder | — | Concurrent SQLite writers on synced folders corrupt databases; rejected. |

## Recommendation

**B**, with C's review page as a follow-on: tombstones make merges correct, and a file the
user moves themselves keeps the no-network rule. Ship import as **preview, then apply**
(the upload flow's parse-before-replace spirit): the import page shows counts of rows that
would be added, changed, cleared, and left alone before anything is written, and keeps a
rollback copy of the previous table.

## Combinations and extremes

| Case | Behaviour under B |
|---|---|
| Same tag on both machines | No change |
| Different projects, different times | Newer wins; preview lists it as "changed" |
| Cleared on one, tagged later on the other | Newer event wins (tag or tombstone) |
| Clock skew between machines | Newer-wins can pick wrongly; the preview shows both values with their times |
| Tag file from another account's export | Conversation UUIDs won't match — rows import as orphan-conversation and surface in the existing orphan banner; preview warns when most rows don't resolve |
| No-Project sentinel | Merges like any project id |
| Inspect mode | Import refused, like every tag write (`_refuse_while_inspecting`) |
| Huge tag files (tens of thousands of rows) | One transaction; preview counts via SQL |
| Malformed or hand-edited file | Refused whole, nothing written (preserve-before-replace) |

## Copy appendix (*proposed*)

- Page title (*proposed*): `Move your tags`
- Intro (*proposed*): "Save your tags to a file, or bring in tags saved on another
  computer. Importing shows what would change before anything is saved."
- Buttons (*proposed*): `Save tags to a file`, `Preview import`, `Apply import`
- Preview headers (*proposed*): `Added`, `Changed`, `Cleared`, `Unchanged`
- Help, Backups (*proposed*): "To combine tagging from two computers, use Move your tags
  instead of copying the database file — copying replaces, importing merges."
