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

## v1.1 — shipped (closed)

Tagged `v1.1.0`. All seven sessions (A–G) shipped; per-session locked decisions below.

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
- **Session F** *(shipped)* — client-side syntax highlighting via vendored highlight.js
  (brief: `docs/briefs/v1.1-session-f-brief.md`; verification matrix:
  `docs/briefs/v1.1-session-f-verification.md`). Locked decisions:
  - The one allowlist rule: `class` survives bleach **only on `<code>`**, and only when the
    entire value matches `^language-[A-Za-z0-9_+#.-]+$` (attribute callable in
    `viewer/render.py`). This *replaced* an unrestricted `class` allowance on `code` and `pre`
    that predated the session — the brief's premise ("class is stripped everywhere") was wrong
    about the starting state, so the change is a pure narrowing, verified by the F2 matrix.
    Do not widen (tags, attributes, pattern, `style`) without a written brief.
  - Vendored under `static/vendor/highlightjs/`: highlight.js **11.11.1** common-languages
    build **plus the upstream `powershell` language module** (soft-gate addition — ~90% of the
    reference export's hinted fences are powershell, which the common build lacks), the
    **GitHub light** theme, and the upstream BSD-3-Clause LICENSE. No CDN, ever.
  - Load order is load-bearing and test-pinned (`tests/test_highlighting.py`): theme CSS
    before `style.css` so app styles win equal-specificity ties; core bundle before the
    powershell module before `highlight-init.js` (deferred scripts run in document order).
  - Init scope is `.markdown-body pre code` only — conversation messages, knowledge docs,
    memory blocks, and the help page; search snippets (`.search-snippet`) are excluded by
    construction and inline code by the `pre` in the selector. The skip-`<mark>` guard is
    load-bearing: highlight.js rebuilds elements from text content, destroying search-hit
    marks, so mark-bearing blocks render unhighlighted by design. One-shot DOMContentLoaded
    init; no observers, no idle-deferral (151 ms on the largest real conversation — no jank).
  - Unregistered hints (toml, batch) fall back to plain text with a console log note —
    expected highlight.js behaviour, left as-is.
- **Session G** *(shipped)* — README rewrite, LICENSE, synthetic-fixture screenshots, version
  bump, tag `v1.1.0` (brief: `docs/briefs/v1.1-session-g-brief.md`). Locked decisions:
  - The README has verified-documentation status (same as the help page and search guide):
    every behavioural claim was checked against the code before commit. Edit it like
    documentation — claims change only when the behaviour does.
  - Screenshots live in `docs/images/` (four PNGs: conversations list, branch switcher,
    search hits, help page), taken from the synthetic fixture via an isolated instance
    (temp `db_path`, fixture `export_source`), footer cropped from every shot and
    `source_paths` pinned to the bare filename. The soft gate on fixture presentability was
    resolved in review: shipped as-is; no purpose-built demo fixture.
  - LICENSE is MIT, copyright "2026 WinterDead". The README's licence section is one line
    pointing at the file.

## v1.1.5 — shipped (closed)

Tagged `v1.1.5`. Housekeeping version; the scope was the roadmap's v1.1.5 block below. All
three sessions (H, I, J) shipped; per-session locked decisions below.

- **Session H** *(shipped)* — documentation drift (brief:
  `docs/briefs/v1.1.5-session-h-brief.md`). Locked decisions:
  - This document's Roadmap section replaced the v1.0-era gate-items list and small backlog.
  - CLAUDE.md carries no version status (plan.md is canonical); the upstream-tool claim is
    past-tense; the repository tree was regenerated with verified annotations;
    "First-session protocol" was deleted.
  - Task 2.4 ("Feature scope" → "What the app is") hard-stopped in-session per the brief:
    thinking blocks were shipped but not described in the README. Closure was deferred to
    Session I with operator dispositions (below). The Session H report itself was not
    committed; its hard-stop substance is recorded in the H commit message and closed here.
- **Session I** *(shipped)* — H residue, vocabulary, graceful startup, help copy (brief:
  `docs/briefs/v1.1.5-session-i-brief.md`). Locked decisions:
  - The README Features list now includes the thinking-blocks bullet; CLAUDE.md's "Feature
    scope" was replaced by "What the app is" — the README is the canonical user-facing
    feature description.
  - Operator dispositions for the old genesis wishlist (2026-07, recorded so they aren't
    relitigated): print-friendly layout is classified "skeleton, unverified — covered by
    roadmap" (the v1.2 design pass verifies it); regex search, case-sensitive toggle, and
    live result counter are **dropped**; copy-whole-conversation is folded into the v1.2
    export design pass as a candidate (see the roadmap bullet).
  - Sort-toggle labels: the canonical set is "Recently updated / Recently created /
    Title (A–Z)". The memory page was harmonised (including toggle order). The alphabetical
    sort on the projects and memory pages stays "Name (A–Z)" — both sort by the project's
    *name*, so forcing "Title" would mislabel the field.
  - The project-detail UI label is "Custom instructions" (Claude product term, matching the
    help copy); code identifiers keep `prompt_template`.
  - Graceful startup: the stored upload and the dev copy fall through the load order on
    `ExportFormatError` with a `WARNING` naming the skipped source, file left in place. A
    skipped stored upload raises a persistent non-dismissible banner (distinct from the
    orphan banner) cleared by a successful upload; the dev copy falls through silently; a
    broken fixture still raises (structural invariant). Tests in `tests/test_startup.py`;
    corrupt fixture `tests/fixtures/corrupt_export.zip` (synthetic bytes).
  - Help troubleshooting rewritten to match: the sample-data entry names both causes, the
    banner entry replaces the startup-crash entry (whose premise no longer exists), and a
    new entry explains Flask's development-server warning. The host binding behind that
    entry's 127.0.0.1 claim was verified at code level (Flask CLI default host, no
    overrides in any start path); Session J pins it with a test.
- **Session J** *(shipped)* — launcher hardening, audit items, close-out (brief:
  `docs/briefs/v1.1.5-session-j-brief.md`; report:
  `docs/briefs/v1.1.5-session-j-report.md`). Locked decisions:
  - `run.sh` venv handling: a failed venv creation removes the partial `.venv` and prints
    Debian-family guidance (`python3-venv`) instead of surfacing a raw ensurepip error; an
    existing `.venv` is trusted only after a `pip --version` probe, and a pip-less venv is
    removed and recreated. `run.ps1` gains the same pip probe; its venv-creation failure
    branch is unchanged — the missing-venv-module condition is Debian-specific (Windows
    Pythons bundle venv), so the Debian message does not apply there. Content guards in
    `tests/test_launchers.py`. These changes return `run.sh` to unverified-on-POSIX; the
    v1.2.5 live-session re-verification covers it.
  - `run.ps1` is UTF-8 with BOM from this session on: the rebuild notice's em-dash must
    survive Windows PowerShell 5.1, which reads BOM-less `.ps1` files as ANSI.
  - Explicit host binding: every start path in the repo (`run.sh`, `run.ps1`,
    `.claude/launch.json`, and both all-busy manual command lines) states
    `--host 127.0.0.1` rather than inheriting the Flask CLI default. The manual commands
    in README.md and help.md stay implicit deliberately (public-voice text, out of this
    session's scope); a canary test fails if the installed Flask CLI's `run` default host
    ever stops being 127.0.0.1.
  - Secret key: verified as bucket "already random per-process" — `secrets.token_hex(32)`
    per `create_app` call, no committed literal, no environment fallback. Pinned by
    `tests/test_secret_key.py` (fresh per start, 256-bit hex). CSRF tokens not surviving a
    restart stays accepted for a localhost single-user tool.
  - Dependency caps: next-major upper bounds in `pyproject.toml` (`Flask>=3.0,<4`,
    `Flask-WTF>=1.2,<2`, `markdown>=3.5,<4`, `bleach>=6.1,<7`); clean-install proof into a
    fresh venv with the import probe passing.
  - Session reports are now part of the session-end checklist (step 8): written to
    `docs/briefs/` alongside the brief and committed with the session's work. The Session
    H and I reports were filed retroactively, verbatim.

## v1.2 — in progress

- **Session K** *(shipped)* — simple inspector: read-only inspect mode (brief:
  `docs/briefs/v1.2-session-k-brief.md`; report: `docs/briefs/v1.2-session-k-report.md`).
  Locked decisions:
  - Inspect mode is a view mode only. `viewer/inspect.py` holds an app-global
    `InspectState` (parsed export, display filename, in-memory FTS connection) under
    `app.config["INSPECT_STATE"]`. The primary `Export` stays resident in
    `app.config["EXPORT"]` throughout, so "Return to my export" just discards the inspect
    state — no reload path runs, and an app restart always comes back on the primary.
  - The no-trace rule holds structurally: `/inspect` uploads are read straight into memory
    and parsed by `viewer.parser.load_export_from_bytes` (new public entry point sharing
    `_parse_zip` with the path loader — same pipeline, same 200 MB cap); the search index
    is an in-memory SQLite database built by the same `index_export`; nothing is written
    under `cache/`, no parse pickle, no `upload_history` row. `tests/test_inspect.py` pins
    `cache/` byte-identical across an inspect session that browses and searches.
  - Noted boundary (flagged in the report): Werkzeug spools multipart bodies over
    ~500 KB to an OS temp file during request parsing — pre-existing behaviour of every
    upload route, deleted at request end, no artifact remains; engineering around it
    would be a stack change (a Session K hard stop).
  - Isolation is enforced twice: `_refuse_while_inspecting()` aborts 403 at the top of
    `/tag`, `/tag/bulk`, `/orphans/purge`, and `/upload` POST, and the templates drop the
    tag dropdowns, tag filter, bulk bar, row checkboxes, project tag counts, the
    project-detail tagged section, and the orphan purge form while `g_inspecting`. The
    Upload page stays in the nav with a notice and a `<fieldset disabled>` form.
  - Tag semantics stay pinned to the primary export: the orphan-banner count and the
    `/orphans` page resolve tags against the primary even while inspecting (resolving
    against an inspected archive would misclassify every row).
  - Mode signal: `.inspect-banner`, blue (`#e8f1fb` / `#2a6fbb`) — third banner colour
    after the amber orphan advisory and the red load-error banner — on every page,
    carrying the byte-exact authored copy and the "Return to my export" POST control.
  - Help: "Inspecting another export" inserted directly after "Loading an export into the
    viewer", reflowed to help.md's one-line-per-paragraph convention (the renderer's
    nl2br would otherwise force line breaks no other section has); "Inspect page" links
    to `/inspect`. Authored copy pinned byte-exact by `tests/test_inspect.py`.
  - Accepted: inspect state is app-global, so concurrent tabs share the one inspected
    view (single-user localhost tool); re-inspecting the same file re-parses.

## Roadmap (post-v1.1)

Sequenced 2026-07; supersedes the earlier "v1.2 — gate items" list. Definition of done for
the core product is v1.2.5 — versions after it are enhancements, not completion.

### v1.1.5 — housekeeping (no new features)

Theme: a clean, public-safe base. Closes exactly as scoped.

- Documentation drift: CLAUDE.md revision (status handling, out-of-scope list, retirement of
  genesis-era sections, personal-references pass) and this roadmap section replacing the old
  gate-items list.
- Vocabulary harmonisation: sort-toggle labels unified to "Recently updated / Recently
  created / Title (A–Z)" where semantics match; "Prompt template" (project-detail UI)
  reconciled with "custom instructions" (help copy, matching the Claude product term).
- Graceful startup on an unparseable stored export: fall through the existing load order
  with a persistent banner instead of raising, keeping the bad file in place so the upload
  flow is the recovery path; ships with the two dependent help-copy edits.
- Launcher hardening, from the 2026-07 POSIX verification: friendly handling of a missing
  venv module (Debian-family `python3-venv`), and venv validation so a half-created,
  pip-less `.venv` is recreated instead of crashing the rerun. Windows launchers checked for
  the same pattern.
- Code-level audit items: secret-key handling, host-binding confirmation (127.0.0.1 only),
  dependency pins.

### v1.2 — simple inspector + conversation export

- Simple inspector *(shipped — Session K, locked decisions above)*: open, inspect, and
  close an export that isn't the main one, without touching the real tagging database.
  Shipped first within the version; it is the test harness for export.
- Conversation export to file: Markdown as the primary format. For PDF, the working lean is
  the browser's print-to-PDF over a polished print stylesheet rather than a PDF-generation
  dependency — confirm in the design pass, which starts by verifying the current state of
  print styling (its existence is a genesis-era claim, never verified as shipped). The design
  pass also covers format details, single vs bulk, branch handling, thinking blocks, tool
  cards, and a privacy review, all before any implementation brief. A copy-whole-conversation
  control (clipboard rather than file) is a design-pass candidate riding the same
  serialisation.

### v1.2.5 — public release

- Delta audit of what v1.2 introduced: inspector path handling; export writing conversation
  content to disk.
- run.sh re-verification on a live USB session (the launcher-hardening changes return it to
  unverified-on-POSIX; the operator procedure exists).
- Demo-fixture decision: optional purpose-built fixture if the synthetic screenshots read
  too plainly.
- GitHub Releases created from the version tags with short changelogs; the repo flips
  public.

### Later (unscheduled, non-blocking)

Order between the two is undecided until development resumes; the current lean is portable
tag state first.

- Artifact + file-tools extraction. Blocked behind a manufactured test corpus: deliberately
  create artifacts of every type — edit histories, rewrites, branch divergence — export, and
  inspect the designed corpus before designing the feature; combine with the file-tools scan
  (`create_file`/`str_replace` bodies) since the two may be one "recover authored content"
  feature. Do not build against the current export's biased sample.
- Portable, mergeable tag state (full profile system). The tag database is the app's only
  irreplaceable state; the export is re-downloadable, the triage work is not. The hard part
  is reconciliation semantics for one user across multiple machines (last-write-wins,
  surfaced conflicts, rollback); needs its own discovery/design pass before any code.
