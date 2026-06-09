# Claude Export Viewer — Plan

> **Provenance note.** The original v1.0 working plan (the section numbering that code comments
> such as "plan §12 step 7" refer to) lived in chat sessions and was never committed. This file
> replaces it as the canonical plan document from v1.1 onward. The §-references in code comments
> are historical; treat the commit history and CLAUDE.md as the record of v1.0 decisions.

## v1.0 — shipped (locked decisions)

Tagged `v1.0.0` (commit `a0de01a`). Feature-complete viewer: parser with preserve-and-flag
handling, branching conversation detail, project list/detail with knowledge docs and prompt
template, manual project tagging (per-row + bulk, CSRF-protected), FTS5 search with
natural-language preprocessing and branch-aware result links, memory view with collapsible
sections, export upload with parse-before-replace and atomic file moves, pickle parse cache
keyed on `(zip_sha256, parser_module_sha256)`, three-bucket TagState with orphan banner and
explicit-confirm purge. 229 tests passing at tag time.

Decisions locked in v1.0 (do not relitigate without a written brief):

- Flask + Jinja2 server-side rendering, no SPA; SQLite + FTS5 via stdlib `sqlite3`.
- Preserve-and-flag over raise for unexpected export data (see CLAUDE.md for the full list).
- Parse-before-replace upload flow; atomic tempfile-rename writes for the zip and pickle cache.
- Tags persist across uploads; orphan rows are surfaced, never auto-pruned.
- Search-snippet highlighting rides the substitute-then-markdown path; `<mark>` stays on the
  bleach allowlist (load-bearing).

## v1.1 — roadmap

- **Session A** *(shipped)* — sender label change ("human" → "User"), search syntax guide,
  upload history log. Locked decisions:
  - Sender labels are display-layer only, via the `sender_label` Jinja filter
    (`viewer/render.py`): "human" → "User", "assistant" → "Assistant", unknown senders pass
    through raw. Parser values, FTS index content, and the pickle-cache key are untouched.
  - The search syntax guide is a native `<details>` block in `templates/search.html`, present in
    all three page states (empty, results, syntax error). Its copy was verified claim-by-claim
    against the live preprocessor + FTS5 — treat the copy as documentation of actual behaviour.
  - Upload history lives in `upload_history` (`cache/viewer.sqlite`, `viewer/history.py`).
    Recorded after successful parse only: uploads always; startup loads of a real export only
    when the sha differs from the latest row; fixture loads never. The `zip_sha256` is the same
    digest that keys the pickle cache, hashed once per flow.
- **Session B** *(shipped)* — "No Project" sentinel tag (brief:
  `docs/briefs/v1.1-session-b-brief.md`; verification matrix:
  `docs/briefs/v1.1-session-b-verification.md`). Locked decisions:
  - Sentinel value is `__no_project__` (`viewer.tagging.NO_PROJECT_SENTINEL`), display label
    **"No Project"** (`NO_PROJECT_LABEL`). Deliberately not a UUID so it can never collide with
    a real project or a fixture UUID. Stored in `conversation_project` like any other tag — no
    schema change.
  - URL slug is `no-project` (`viewer.routes.NO_PROJECT_SLUG`); `?tag=no-project` is the only
    public spelling and maps to the sentinel internally. Keep it stable.
  - Orphan-semantics split: orphan-conversation detection still applies to sentinel rows (a
    sentinel row whose conversation is missing is orphan-conv, purgeable, never badged
    "project also missing"), but orphan-project detection never does — the sentinel "project"
    exists by definition, so sentinel rows on live conversations are bucket `live`.
  - The sentinel never appears on `/projects` or in home stats; its project-detail URL
    redirects to `/conversations?tag=no-project`.
  - The `/tag` and `/tag/bulk` write-path validation allowlists exactly the export's projects
    plus the sentinel (the brief's B1 verification found the project-set check at those two
    routes; the allowlist was extended rather than the sentinel scheme changed).
- **Session C** *(shipped)* — `/help` page in the nav (brief:
  `docs/briefs/v1.1-session-c-brief.md`; verification matrix:
  `docs/briefs/v1.1-session-c-verification.md`). Locked decisions:
  - The help copy lives in `viewer/content/help.md`, read per request and rendered through the
    same markdown → bleach pipeline as every other blob. Treat the copy as documentation of
    actual behaviour (same status as the Session A search guide): it was verified
    claim-by-claim, and the four discrepancies found were resolved as user-approved copy edits
    recorded in the verification matrix.
  - Page structure comes from `viewer.render.split_help_page`: only ATX `##`/`###` are
    structural (fence-aware; `####`+ stays in content). Section anchors are
    `<section-slug>` and `<section-slug>--<sub-slug>` via `slugify` — they are public deep-link
    targets (`/help#tagging-and-the-triage-workflow`), keep them stable.
  - The template reuses the memory page's section machinery wholesale (memory-block classes,
    `static/memory.js` anchor-aware expansion). Top-level sections collapsed by default,
    sub-sections expanded. Page-level expand/collapse-all uses `.help-section-toggle`;
    collapse-all returns the page to its default state (sub-sections reset to open).
  - Verify-negative tests in `tests/test_help.py` guard the public-trajectory rule at the
    template level: no drive-letter paths, no "Dropbox", no machine usernames (checked
    dynamically via `getpass` so nothing personal is committed). The test fixture pins
    `Export.source_paths` to a bare filename because the footer echoes it verbatim.
- **Session D** *(shipped)* — artifact-encoding discovery against the real export (no app
  code; brief: `docs/briefs/v1.1-session-d-brief.md`; deliverable: `docs/artifact-encoding.md`).
  Findings and locked decisions:
  - The reference export's artifact calls are create-only `text/markdown` (34 calls across 28
    conversations); zero `update`/`rewrite` commands exist anywhere in it, so edit-replay
    semantics remain unverified inference — any future consumer treats those commands as
    unknown-shape (preserve-and-flag) until an export containing them is scanned.
  - Artifact `tool_use`/`tool_result` ids are null in the export — pairing is positional (the
    result directly follows its call in the same message). Every observed result is a bare
    `"OK"` acknowledgement.
  - Truncation verdict: **no evidence of truncation** (six checks documented in the doc; the
    strongest is that all 3,084 `md_citations` offsets fall within content bounds).
  - Reconstruction verdict: **feasible** — the final state of every observed artifact is
    literally its `create` call's `content`. Recommendation recorded as v1.2 planning input;
    branch semantics (three candidate rules in the doc) deliberately left undecided.
  - `tools/scan_artifacts.py` committed as a generic re-check utility (stdlib-only,
    aggregate-only output) for the next time the export format shifts.
- **Session E** *(shipped)* — double-clickable launcher scripts (brief:
  `docs/briefs/v1.1-session-e-brief.md`; verification matrix:
  `docs/briefs/v1.1-session-e-verification.md`). Locked decisions:
  - Script names: `run.ps1` (the real Windows logic), `run.bat` (one-line shim invoking
    PowerShell with a per-invocation `-ExecutionPolicy Bypass`, plus `|| pause` so a
    double-clicked window stays open on failure), `run.sh` (bash, macOS/Linux) — all at the
    project root.
  - Port-fallback list is `5000, 5050, 5051, 5052`; the chosen port is printed when it isn't
    the default, and all-busy exits with the manual `--port` instruction.
  - No-system-changes constraint: the scripts never touch anything outside the project folder —
    no PATH edits, no policy changes, no installs beyond the local `.venv`; a missing/too-old/
    Store-stub Python gets a friendly message pointing at python.org, never an auto-install.
  - Install step is import-probed (`import flask, flask_wtf, markdown, bleach`), so a run with
    a healthy venv skips pip entirely and a half-finished first install self-heals.
  - `.gitattributes` pins `run.sh` to LF (and the Windows scripts to CRLF) against
    `core.autocrlf`; `tests/test_launchers.py` guards file presence, the shim wiring, and the
    public-trajectory verify-negatives.
- **Session F** — client-side syntax highlighting via vendored highlight.js.
- **Session G** — README rewrite, LICENSE, synthetic-fixture screenshots, tag v1.1.0.
  Screenshot rule: screenshots must come from the synthetic fixture and must avoid or crop the
  footer — it echoes the local source path verbatim.

Small backlog (unscheduled, not part of any session above):

- **Polish** — harmonise sort-toggle vocabulary across the conversations, projects, and memory
  pages to the "Recently updated / Recently created / Title (A–Z)" pattern where semantics
  match. Same pass: reconcile the "Prompt template" label (project-detail UI) with "custom
  instructions" (help copy, matching Claude.ai's product term) — surfaced in the Session C
  verification matrix.
- **Behaviour** — graceful startup on an unparseable stored export: instead of raising, fall
  through the existing load order, surface a persistent banner, and keep the bad file in place
  so the Upload flow (parse-before-replace) becomes the recovery path. Update the two
  help-page troubleshooting entries ("I see sample data…" and "The app fails to start…") when
  it ships.

## v1.2 — gate items (out of scope for v1.1)

- **Public-readiness audit** before the repo goes public: secret-key handling, host-binding
  confirmation, dependency pins.
- **Artifact extraction** (Session D recommendation: feasible). Minimal scope proposed in
  `docs/artifact-encoding.md`: reconstruct final state along the rendered path, render real
  content in the placeholder cards, per-artifact download named from `title`/`type`,
  preserve-and-flag for unobserved commands. Requires a decision on branch semantics (three
  candidate rules in the doc) before implementation.
- **File-tools discovery pass** (planning input for archive recovery). Mirror Session D's
  method against `create_file` / `str_replace` (and related file-tool) calls in the export:
  their inputs carry full file bodies and now outnumber artifact calls several-fold, so they
  may matter more than artifacts for recovering work from the archive. `tools/scan_artifacts.py`
  is the starting point to generalise into a scanner for these tool names.
