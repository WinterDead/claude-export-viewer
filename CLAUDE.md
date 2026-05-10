# Claude Export Viewer

Local Python web app for reading Claude.ai data exports (the zipped JSON archive Anthropic emails when you request your data). Inspired by `osteele/claude-chat-viewer`, but adds support for project-grouped conversations and the memory file — both of which the upstream tool currently ignores.

## Status

Greenfield. No code yet. First-session goal is to inspect a real export ZIP and design the data model from ground truth, not from assumptions about format.

## Constraints

- Python 3.11+ in a project-local `.venv/`
- Flask + Jinja2 server-side rendering, no SPA
- SQLite with FTS5 for full-text search (stdlib `sqlite3`)
- Personal use, localhost only — no auth, no deployment, no telemetry
- All data stays on the machine; no external network calls at runtime
- Windows host, so paths use `.venv\Scripts\python.exe` not `.venv/bin/python`

## Coding conventions

- Canadian English in user-facing text, comments, and docs (colour, behaviour, centre, defence, organisation). Code identifiers may follow library conventions where it would be jarring to deviate (e.g., `args.color` if a CLI library uses that spelling).
- Type hints on public functions
- Line length 100, format with `black`, lint with `ruff`
- `pytest` for tests; the parser module is the priority for test coverage. The UI does not need tests.
- Commits authored as "WinterDead"
- **Preserve and flag, don't silently drop.** When the parser meets unexpected data (unknown enum values, references to missing entities, structural surprises), preserve it on the result — in a typed slot if one exists, or an "orphan" collection on the parent — and log a `WARNING` naming what was preserved. Let the renderer decide whether to surface it. Reserve raising for true structural invariants (a required file is missing, a list-of-1 contains zero or two records). The export has no version markers and may shift; preserve-and-flag is what keeps the viewer working through small format changes. Existing examples to follow: unknown content-block types, orphan `parent_message_uuid`, parent-graph cycles, dangling project memories, unknown sender values, account-uuid mismatches, non-zero batch index in isolation.

## Proposed structure (finalize after inspecting the export)

```
claude-export-viewer/
├── app.py                 # Flask entry point
├── viewer/
│   ├── parser.py          # Read ZIP, normalize JSON, build typed records
│   ├── search.py          # SQLite FTS5 index + queries
│   ├── routes.py          # HTTP routes
│   └── render.py          # Markdown, code highlighting, artifact extraction
├── templates/             # Jinja2
├── static/                # CSS, minimal JS
├── tests/
├── data/                  # user's export ZIP (gitignored)
└── docs/                  # design notes, schema reference
```

## Feature scope

Parity with `osteele/claude-chat-viewer`:
- ZIP upload (no extraction needed)
- Conversation list with summaries
- Single conversation view with code blocks, artifacts, thinking blocks
- Title/summary search, full-text search, regex, case-sensitive toggle, live result counter
- Copy whole conversation, download artifacts as ZIP
- Print-friendly layout

Net-new (the gaps that motivate this project):
- **Project grouping.** Conversations that lived inside a Claude Project should be browsable by project, not just listed flat.
- **Memory file view.** The export contains a memory file (the dump of what Claude has stored across conversations); render it readably.

## First-session protocol (read before writing any application code)

The export ZIP format has evolved and isn't reliably documented. Web sources contradict each other. Before designing data models, schemas, or routes:

1. Locate the export ZIP at `data/export.zip`.
2. Inventory it: list every file in the archive, identify each top-level JSON file, and sample 1–3 records from each. Note field names, value shapes, and any version markers.
3. For each conversation, identify how messages are structured — text, code, artifacts (`tool_use` / `tool_result` content blocks), thinking blocks, attachments.
4. Identify how project membership is represented for conversations: a `project_uuid` field on each conversation? A separate `projects.json`? Directory grouping inside the ZIP?
5. Identify the memory file's filename and structure (Markdown? JSON? plain text?).
6. Write findings to `docs/export-schema.md`, including a small synthetic fixture the parser tests can use.

Only after the inventory exists should we design schemas, write the parser, or scaffold routes.

## Out of scope

- Multi-user support
- Authentication of any kind
- Cloud deployment
- Modifying the export ZIP
- Markdown / PDF export of conversations (defer; possible follow-up)
