# Claude Export Viewer

Local Python web app for reading Claude.ai data exports (the zipped JSON archive Anthropic emails when you request your data). Inspired by `osteele/claude-chat-viewer`, but adds support for project-grouped conversations
and the memory file — gaps in the upstream tool at the time this project started.

## Status

Feature-complete and versioned. `docs/plan.md` is the canonical record of the current
version, roadmap, and locked decisions; per-session briefs live under `docs/briefs/`. This
file deliberately carries no version status, so it never drifts.

## Constraints

- Python 3.11+ in a project-local `.venv/`
- Flask + Jinja2 server-side rendering, no SPA
- SQLite with FTS5 for full-text search (stdlib `sqlite3`)
- Personal use, localhost only — no auth, no deployment, no telemetry
- All data stays on the machine; no external network calls at runtime
- Primary development host is Windows, so development commands use
  `.venv\Scripts\python.exe`; the launchers (`run.sh`, `run.ps1`, `run.bat`) cover end users
  on all three platforms

## Coding conventions

- Canadian English in user-facing text, comments, and docs (colour, behaviour, centre, defence, organisation). Code identifiers may follow library conventions where it would be jarring to deviate (e.g., `args.color` if a CLI library uses that spelling).
- Type hints on public functions
- Line length 100, format with `black`, lint with `ruff`
- `pytest` for tests; the parser module is the priority for test coverage. The UI does not need tests.
- Commits authored as "WinterDead"
- **Preserve and flag, don't silently drop.** When the parser meets unexpected data (unknown enum values, references to missing entities, structural surprises), preserve it on the result — in a typed slot if one exists, or an "orphan" collection on the parent — and log a `WARNING` naming what was preserved. Let the renderer decide whether to surface it. Reserve raising for true structural invariants (a required file is missing, a list-of-1 contains zero or two records). The export has no version markers and may shift; preserve-and-flag is what keeps the viewer working through small format changes. Existing examples to follow: unknown content-block types, orphan `parent_message_uuid`, parent-graph cycles, dangling project memories, unknown sender values, account-uuid mismatches, non-zero batch index in isolation.

## Public-repo trajectory

The repo goes public around v1.2. From now on: all user-facing text, documentation, and fixtures
are written for a general audience, not just the original developer. No personal file paths,
usernames, machine names, or real export data in anything committed. Screenshots, when needed,
come only from the synthetic fixture, never from a real export.

## Session-end checklist

At the end of every session:

1. Run the full test suite; all tests pass.
2. Run ruff and black; both clean.
3. `git status` — review everything staged or untracked.
4. Privacy grep on staged files before committing:
   `git diff --cached --name-only | Select-String -Pattern "(data/|cache/|sqlite|venv|secret|token|api[_-]?key|\.env|password|credential)"`
   If it returns anything, stop and fix `.gitignore` before committing.
5. If the session read the real export's *content* (not just metadata), additionally grep the
   staged **diff content** for leak patterns before committing:
   `git diff --cached | Select-String -Pattern "(conversations\.json|projects\.json|users\.json|memor(y|ies)|uuid)"`
   and eyeball every hit — the point is to catch real conversation text, titles, or identifiers
   that slipped into docs, fixtures, or comments. Filenames alone (step 4) don't catch this.
6. Commit with a descriptive message. Do not leave sessions uncommitted.
7. Update the plan document (`docs/plan.md`): mark shipped items as locked decisions, prune
   obsolete detail.

## Repository structure

```
claude-export-viewer/
├── app.py                 # Flask app factory; startup load order: uploaded export → dev copy → fixture
├── viewer/                # Application package: parser, parse cache, tagging, upload history, FTS5 search, routes, rendering, help content
├── templates/             # Jinja2 templates (server-side rendering)
├── static/                # CSS, page JS, vendored highlight.js
├── tests/                 # pytest suite + synthetic fixtures
├── tools/                 # scan_artifacts.py — stdlib scanner for artifact calls in an export
├── docs/                  # plan, export-schema reference, artifact-encoding study, briefs, screenshots
├── run.ps1, run.bat, run.sh  # double-clickable launchers (Windows / macOS / Linux)
├── pyproject.toml         # packaging, dependencies, black/ruff/pytest config
├── README.md              # user-facing documentation
├── CLAUDE.md              # this file: development instructions and conventions
├── LICENSE                # MIT
├── .claude/               # dev-server launch config for tooling
├── .gitattributes         # line-ending pins for the launchers; vendored assets exempt from EOL conversion
├── .gitignore             # excludes data/, cache/, .venv/, and derived state
├── data/                  # gitignored — optional dev-convenience export copy (data.zip)
└── cache/                 # gitignored — uploaded export, parse cache, tag/history database
```

## What the app is

A local viewer for Claude.ai data exports: browse conversations (including branched ones),
projects with their knowledge docs and prompt template, and the memory file; tag
conversations by project; full-text search across everything. The README is the canonical
user-facing feature description; this file carries only what development sessions need.

## Out of scope

- Multi-user support
- Authentication of any kind
- Cloud deployment
- Modifying the export ZIP
