# Claude Export Viewer — Plan

> **Provenance note.** The original v1.0 working plan (the section numbering that code comments
> such as "plan §12 step 7" refer to) lived in chat sessions and was never committed. This file
> replaces it as the canonical plan document from v1.1 onward. The §-references in code comments
> are historical; treat the commit history and CLAUDE.md as the record of v1.0 decisions.

> **History rewrite, 2026-07.** All history was rewritten once before the public
> release (author-email correction and a fixture-string scrub); commit hashes cited in
> documents and briefs written before the rewrite are pre-rewrite identifiers, kept as
> historical labels.

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

## v1.2 — shipped (closed)

Tagged `v1.2.0`. All four sessions (K–N) shipped; per-session locked decisions below.

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
- **Session L** *(shipped)* — export discovery: design record, content-block census,
  print-CSS assessment (brief: `docs/briefs/v1.2-session-l-brief.md`; report:
  `docs/briefs/v1.2-session-l-report.md`). No application code changed (hard stop honoured).
  Locked decisions and findings:
  - `docs/export-design.md` committed byte-exact as the chat-authored design record for the
    conversation-export feature; it is edited only through the same chat-authored channel.
    Its structural decisions (assemble stored Markdown, browser downloads only, one-line tool
    cards, thinking default-off, WYSIWYG branch capture) are design-locked pending the
    implementation sessions' verification matrices.
  - Census (`tools/scan_content_blocks.py`, `docs/content-block-census.md`): **no unmapped
    content-block types — the design record's rendering table covers everything observed; no
    rendering decisions required before Session M.** Four block types only
    (text/thinking/tool_use/tool_result), two senders, zero truncated/cut-off thinking flags
    (the flag path is fixture-test territory), all attachments under 100 KB with non-empty
    extracted text. Artifact command census reproduces the Session D baseline exactly
    (34 `create` in 28 conversations, zero `update`/`rewrite`) — no format shift.
  - Naming clarified in passing: the dev copy `data/data.zip` is the older May 2026 archive
    (`docs/export-schema.md`'s reference); the uploaded export in `cache/` is the current
    reference export and the generation Session D scanned.
  - Print assessment (`docs/print-css-assessment.md`): the skeleton's force-open `<details>`
    hack is dead in modern engines (`::details-content` + `content-visibility: hidden`;
    verified empirically in the app's own Chromium) — closed thinking/attachment blocks print
    as unlabelled empty husks, and summary-hiding is cascade-inconsistent. The polish pass is
    scoped as a recommendation list; its core is deliberate WYSIWYG printing with visible
    summaries (the design record's thinking-alignment requirement) and an explicit
    do-not-resurrect note for blanket force-open via the modern pseudo-element.
- **Session M** *(shipped)* — conversation-export serializer + download endpoints (brief:
  `docs/briefs/v1.2-session-m-brief.md`; report: `docs/briefs/v1.2-session-m-report.md`).
  Backend only — no UI, no template edits; Session N owns the UI entry points, print polish,
  and the README/help updates. Locked decisions:
  - `viewer/export.py` serialises the *stored* Markdown directly (Flask-free, same rule as
    `viewer/render.py`); the markdown → bleach pipeline is never involved, so the feature
    adds no sanitiser surface. `ExportOptions` defaults are design-locked and test-pinned:
    `include_thinking=False`, `include_attachment_text=True`.
  - Endpoints: `GET /conversations/<uuid>/export` (WYSIWYG — accepts the same `branch`
    parameters as the detail page) and `POST /conversations/export` (CSRF-protected; zip
    assembled entirely in memory, one `.md` per conversation on its default branch path).
    Both work while inspecting — they read via `_current_export()` and write nothing; the
    Session K no-trace snapshot holds with exports in the cycle (test-pinned).
  - Error-result parity (the brief's Task 1.2 verification target): the reading view marks
    error results (` · error` in the card meta plus an `is-error` class), so the export card
    carries the same ` · error` marker. Tool pairing is positional (the Session D finding),
    tolerates an absent result (`— no result recorded.`, byte-exact per the brief), and
    renders an orphan `tool_result` as its own card (dormant; none exist in real data).
  - Frontmatter conditional scoping: `thinking`/`attachments`/`branch_points` trigger on what
    the *conversation* has (all branches — the design record's literal wording); `messages`
    counts the exported path, as the spec states. The `project` field carries a live tag's
    project name only — absent for untagged conversations, the No-Project sentinel (not a
    project), orphan tags (no label to resolve), and always while inspecting (tags never
    pair with an inspected export, per Session K).
  - Naming: file dates are the conversation's created date as stored (UTC — the same instant
    the `created` field carries); `exported` and the zip name use the export-day local date.
    The bulk collision fragment is the *last* eight uuid characters (the synthetic fixtures
    share their leading digits). The `generator` version reads `pyproject.toml` via
    `tomllib`; installed-package metadata goes stale under editable installs and is only the
    fallback.
  - The branch annotation's `response(s)` is exported as literal spec text for any N;
    flagged for Session N as candidate copy polish. The copy-whole-conversation control
    remains undecided and falls to Session N with the rest of the UI. *(Both resolved in
    Session N — see below.)*
- **Session N** *(shipped)* — export UI, print polish, copy fixes, parser alignment,
  README/help, close-out (brief: `docs/briefs/v1.2-session-n-brief.md`; report:
  `docs/briefs/v1.2-session-n-report.md`). Version bumped to 1.2.0; tagged `v1.2.0`.
  Locked decisions:
  - Detail-page Export control is a plain GET form to the single-export endpoint,
    re-emitting the page's `branch` parameters (WYSIWYG). Both export surfaces use
    checkbox + hidden-0 pairs with the checkbox FIRST: the endpoints read the first
    submitted value, so unticking submits an explicit off instead of falling back to the
    default. Labels byte-exact — **Include thinking blocks** (unchecked), **Include
    attachment text** (checked) — and template-level test-pinned.
  - The conversations-list action bar now renders in inspect mode carrying only
    **Export selected** (via `formaction` to the bulk endpoint; the form's default action
    switches to the export endpoint while inspecting). Row checkboxes render in both modes
    (aria-label neutralised to "Select conversation"); `bulk_tag.js` null-guards the
    tagging controls, which stay structurally absent while inspecting (Session K contract
    updated in `tests/test_inspect.py` for exactly this action).
  - **Copy conversation shipped** (resolving the design record's copy-control candidate):
    clipboard delivery of the single-export output for the current branch combination and
    checkbox states, without frontmatter. `serialise_conversation` gained
    `include_frontmatter` (default true; query parameter `frontmatter=0` on the GET
    endpoint); the H1 title stays either way. Brief "Copied" confirmation; wiring in
    `static/conv_actions.js` (which also hosts the **Print / Save as PDF** button's
    `window.print()`).
  - Print polish (all operator-ratified): **deliberate WYSIWYG** — both old `<details>`
    print rules deleted; the assessment's do-not-resurrect note is binding and test-pinned
    (comments stripped, no `::details-content` rule anywhere). Branch switchers print
    their pluralised label line only (option list and the colon-span hidden); message
    timestamps don't print (operator-chosen None default; the None/Absolute/Relative
    toggle is deliberately not built — queued for 1.2.6+); `pre` wraps instead of
    clipping; `.memory-block-controls` and the new `.conv-actions` hidden; the no-op
    `section` break rule pruned; chrome hide-list and flattened message styling kept.
  - Links appendix (`static/print_links.js`): print-only `[N]` markers after content
    links (`.markdown-body` scope) and a "Links" section at the end of the conversation,
    `N. <link text> — <full URL>`, repeated URLs sharing one number. Built on page load
    and rebuilt on `beforeprint`, so links inside collapsed sections appear only if the
    section is open at print time — consistent with WYSIWYG. Screen rendering unchanged
    (`.print-link-marker`/`.print-links` are `display: none` outside print).
  - C1 (operator decision): a conversation tagged **No Project** exports with
    `project: "No Project"`; orphan tags and inspect mode stay field-absent.
    `docs/export-design.md`'s `project` comment amended byte-exact.
  - C2: the branch annotation is properly pluralised on both surfaces (export
    annotation and print label line, byte-exact per the brief); design record amended.
  - Task D: the parser **preserves** unknown content-block types as
    `UnknownBlock(type, raw)` (repr hides `raw` — house rule), with the WARNING retained
    ("preserved as UnknownBlock"). Both placeholder paths are reachable from a parsed
    archive and test-pinned: the reading view's card now names the raw type; the export
    emits `[Unrecognised content block: <type>]`.
  - README gained the three authored feature bullets (inspect mode, conversation export,
    print/PDF; lead terms bolded to match the list convention — mechanical soft gate);
    help.md gained the authored "Exporting conversations" section directly after
    "Reading a conversation". Both verified claim-by-claim (report matrix).

## v1.2.5 — shipped (closed)

Tagged `v1.2.5`. Both sessions (O, P) shipped; per-session locked decisions below. The
version closes the core product's definition of done; the public flip is **pending
operator action** — the flip itself is never performed by a session.

- **Session O** *(shipped)* — public-flip delta audit + operator pass fixes (brief:
  `docs/briefs/v1.2.5-session-o-brief.md`; report: `docs/briefs/v1.2.5-session-o-report.md`).
  No version bump, no tag. Locked decisions and findings:
  - Nav order (operator decision, B1): Home, Conversations, Projects, Memory, Search,
    Upload, **History**, Inspect, Help — History directly right of Upload. Test-pinned
    (`tests/test_history.py`).
  - Export-control grouping (B2): the two toggles plus **Export** and **Copy
    conversation** sit in a fieldset with byte-exact legend **Export options**;
    **Print / Save as PDF** sits outside the group *and* the form (screen-WYSIWYG, the
    toggles don't govern it). Test-pinned (`tests/test_export_form_e2e.py`).
  - Summary rides the frontmatter (operator-ratified, no toggle — B4): directly after
    `title`, only when the conversation has one, double-quoted YAML-safe scalar. Export
    carries it as metadata; Copy (frontmatter off) omits it; Print keeps showing it.
    Design record and help.md amended byte-exact; the summary-placement toggle is queued
    under 1.2.6+.
  - **B3/B5 verdicts — no code defects.** The export toggles work through the real
    rendered forms on all three surfaces (verified in a driven Chromium; the operator's
    observation traces to the attachments-vs-binary-files reach split — binary files are
    never included, by design, and dominate the real archive 41:7). The links appendix
    works end-to-end in real print rendering (verified by print-to-PDF; the operator's
    observation traces to bare URLs — 91 of 213 real conversations carry bare URLs with
    no markdown-form link, and bare URLs never become anchors). Both pinned: four-state
    end-to-end form tests (closing the hand-built-parameters test gap) and the JS↔CSS
    print-links content guard. Follow-up copy/linkify questions are operator decisions
    recorded in the Session O report, folding into the 1.2.6+ queue.
  - Audit verdicts: export write paths, log content, security pins, and the whole-tree
    public-trajectory sweep all clean; the Werkzeug multipart spool (Session K's recorded
    input) documented with deletion verified in the installed stack — the help page's
    "nothing saved to disk" stays fair. Dependency audit: the four ranges freshly resolve
    clean; the dev venv's bleach 6.3.0 carried three advisories and was bumped to 6.4.0
    **within the existing `<7` cap** (soft gate; caps unchanged). Screenshots: all four
    stale, `conversation-branches.png` and `help.png` now misleading — retakes belong to
    the close session (public voice).
- **Session P** *(shipped)* — close-out, two-phase (brief:
  `docs/briefs/v1.2.5-session-p-brief.md`; report:
  `docs/briefs/v1.2.5-session-p-report.md`). Phase 1 implemented and staged with nothing
  committed; Phase 2 committed, bumped to 1.2.5, tagged `v1.2.5`, and pushed on operator
  go-ahead. Locked decisions:
  - Launcher verification is **complete on both platforms** (POSIX 2026-07-06, Windows
    2026-07-06, operator reports in backups) — closes the roadmap's run.sh
    re-verification item.
  - The push-gap process finding produced P1: the remote was found nineteen commits and
    two tags stale during the 2026-07-06 launcher verification — the session-end
    checklist said commit, never push. CLAUDE.md step 6 now requires pushing the branch
    and all tags to origin.
  - Attachment-toggle clarity: byte-exact `title` tooltip on the **Include attachment
    text** control on both surfaces it renders (detail-page fieldset and
    conversations-list bulk bar), plus the byte-exact help.md sentence appended to
    "Single conversations". The claim was verified before staging: the serializer's
    toggle gates text attachments only (`_files_segment` is unconditional and
    names-only), the parser's `FileRef` carries uuid + name — the archive holds no
    binary content — and the reading view's files notice states the same.
  - Screenshot retakes: all four README shots re-taken from the synthetic fixture via an
    isolated instance per the Session G locked decisions (fixture `export_source`, temp
    `db_path`, `source_paths` pinned to the bare filename, tags seeded through the real
    `set_tag` API, footer excluded from every shot — hidden at capture time, same
    visible outcome as cropping). `help.png` is deliberately full-page height now: the
    current section list is the point, after Session O's A7 found the old crop
    misleading for hiding sections. README references and alt texts verified unchanged
    and accurate.
  - The demo-fixture roadmap decision closes with the retakes: the synthetic-fixture
    shots were operator-reviewed at the phase boundary and shipped; no purpose-built
    demo fixture.

## v1.3.0 — in progress

Arc: new export format support, dark mode, codebase review. Renumbering note: this version
was provisionally "1.2.6"; the operator ratified "1.3.0" in 2026-08 — a whole-archive
format change is a minor version by this project's semver rhythm, and the reserved patch
series keeps its role under the name "1.3.x". No version bump until the arc's close session.

- **Session R, Phase 1** *(shipped)* — new-format discovery (brief:
  `docs/briefs/v1.3.0-session-r-brief.md`; report:
  `docs/briefs/v1.3.0-session-r-phase1-report.md`). Locked decisions and findings:
  - `tools/scan_export_schema.py` committed: stdlib-only, aggregate-only schema profiler
    accepting a legacy single-zip export, a manifest `.json`, or a directory holding a
    new-format export set (any subset of category zips; multi-part grouping is structural
    tolerance, unverified). Privacy is by construction: machine-enum allowlist with a
    shape check, UUID masking of values and dict keys, masked placeholders elsewhere;
    `memory_files[].path` gets a top-level-segment-only census.
  - All nine chat-side verification targets reproduced (confirm/diverge table in the
    Phase 1 report). Notable format facts beyond the chat-side scan: `memory_files`
    paths are absolute-style (leading `/`); the manifest carries `version`,
    `created_at`, `instructions`, `total_files` beyond its file list, and its
    `batch_index` enumerates files 0–3 while `part` is 0 throughout; new block-level
    keys (`approval_key_legacy`, `hidden`, `hidden_in_chat`, `thinking_hidden`,
    `tool_identifier`, `tool_origin`, `citations_grouping_mode`); `meta` carries an
    `output_format_category` enum; knowledge inner blocks gain `links`;
    `structured_content` can be a dict (file-tool targets); the project-membership gap
    holds in the new format (exactly the legacy seven conversation keys).
  - `docs/export-schema.md` extended to cover both container formats, every field
    marked (both) / (legacy only) / (current only); all examples synthetic.
  - Open design questions recorded verbatim in the Phase 1 report for the chat design
    record; design work deliberately out of session scope.
- **Session R, Phase 2** *(shipped)* — read-only codebase review (report:
  `docs/briefs/v1.3.0-session-r-phase2-report.md`). No application code changed. Ranked
  findings feed the arc's implementation briefs; headlines: the parse→model seam is clean
  (all consumers are container-agnostic via `Export`; the leaks concentrate in
  `_parse_zip`, upload/inspect routes, and single-zip cache/history fingerprinting);
  theming is substantially ready (7 root tokens, 162 `var()` uses, zero inline/JS colours;
  ~66 hardcoded declarations, a dark highlight.js theme, and a print token reset are the
  work); the test suite needs conftest consolidation + a parametrisable export fixture
  before dual-format fixtures land; runtime dependency closure audits clean; README/help
  claims the new format invalidates are enumerated for the close session.

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
- Conversation export to file: Markdown as the primary format. The design pass is complete —
  `docs/export-design.md` is the design record (Session L, locked decisions above) — the
  serializer plus both download endpoints shipped in Session M, and Session N shipped the
  rest (locked decisions above): the UI entry points, the print-polish pass scoped by
  `docs/print-css-assessment.md`, the README/help updates, and the copy control (shipped —
  clipboard delivery, frontmatter off). The version closed with the `v1.2.0` tag.

### v1.2.5 — public release

- Delta audit of what v1.2 introduced: inspector path handling; export writing conversation
  content to disk. *(done — Session O, verdicts above; adds screenshot retakes to the close
  session's list.)*
- run.sh re-verification on a live USB session (the launcher-hardening changes return it to
  unverified-on-POSIX; the operator procedure exists). *(done — operator verification
  complete on both platforms, 2026-07-06; recorded in Session P.)*
- Demo-fixture decision: optional purpose-built fixture if the synthetic screenshots read
  too plainly. *(resolved — Session P: retakes from the synthetic fixture shipped after
  operator review at the phase boundary; no purpose-built fixture.)*
- GitHub Releases created from the version tags with short changelogs; the repo flips
  public. *(pending operator action — the flip is never performed by a session.)*

### v1.3.0 — new export format, dark mode, codebase review

Scoped and ratified by the operator, 2026-08. Anthropic changed the export container
format (manifest + four category zips replacing the single zip) and the memory system
(the app's supported structure is now the Legacy Memory System; the new system exports
`memory_files`). See `docs/export-schema.md` for both formats.

- Dual-format support: parse both containers into one internal model, including the new
  memory system (`memory_files` open-set taxonomy, `conversations_memory`,
  `project_memories`).
- Login history displayed (ratified *that* it displays; *how* is an open design
  question).
- Multi-file upload accepting any subset of the category zips with a degraded-view
  banner; the manifest optional. **(Provisional — approved as the starting shape pending
  the design record and operator testing.)**
- Dark mode.
- Development briefs excluded from the default GitHub download via `export-ignore`
  (streamlined download, files remain browsable; no separate branch or repo). *(Shipped
  ahead of the arc — commit "Exclude development briefs from generated archives" covers
  `docs/briefs/**`; the briefs-only scope was ratified with the Session S brief.)*
- The codebase review (Session R Phase 2) feeding the arc's implementation briefs.
- **Session S (implementation, in progress).** `docs/format-support-design.md` (ratified
  2026-08-25) is the arc's normative design record, committed verbatim at the session's
  open. Phase 1 shipped *(locked)*: conftest consolidation — shared `export_path` /
  `export` / `app` / `client` / `app_factory` fixtures and one CSRF scraper in
  `tests/conftest.py`, with `export_path` as the container-format parametrisation seam;
  monkeypatch hygiene for the module-global rebinding tests; the fixture regeneration
  guard (`tests/test_fixtures.py`, both directions); `/inspect` added to
  `test_highlighting.py`'s asset matrix; and the current-format fixture sets
  (`tests/fixtures/current/`, `current_two_part/`, `current_dangling/`) emitted by the
  extended registry in `build_fixtures.py`. Phase 2 shipped *(locked, except where the
  record marks provisional)*: content-based container detection (record §2;
  `sniff_container`/`sniff_manifest`), the sibling current-format set loader beside the
  untouched legacy path (any subset loads, per-container required-files, dict-shaped
  memories, `-NNN` part warnings), multi-part merge with duplicate-uuid
  preserve-and-flag *(provisional — no multi-part export observed)*, the record §6 model
  slots (typed `injected_prompt_block`, thinking summaries/`thinking_hidden`, tool
  `display_content` and `output_format_category`, raw per-block timestamps, memory
  files, login events, `Export.categories`; `user`/`global_memory` now optional for
  subset loads) with the deliberate signature drop test-pinned end to end, dual-format
  parametrisation of nine test families, and the aggregate-only real-data skip test
  (`data/export-2026-08/`, Session R confirm-table counts — passing). Phase 3 shipped
  *(provisional where the record marks the upload shape provisional — operator testing
  still gates it)*: multi-file upload and inspect (one picker for category zips plus the
  optional manifest, content sniff-and-slot, duplicate-category last-wins with a logged
  warning, atomic stage-parse-swap generalised to sets); set storage under
  `cache/active_export/` with the pre-set `active_export.zip` still picked up by
  tolerance; the set fingerprint keying parse cache, history rows, and startup dedup
  (set-of-one digests continuous with pre-set rows — test-verified against a
  pre-existing history row); the `VIEWER_MAX_UPLOAD_MB` per-file cap knob (default 200,
  both enforcement sides, stale comment corrected); the degraded-view banner with the
  Appendix C1 copy byte-exact, manifest expected-vs-supplied included, following the
  viewed export (inspected subsets included); inspect no-trace extended to sets; and
  real-rendered-form tests for both pickers. Phase 4 (rendering and copy) follows under
  the same brief.

### Later (unscheduled, non-blocking)

- **1.3.x patch series (post-1.3.0) (reserved — recorded prominently so it isn't
  forgotten between work windows).** Renamed from "1.2.6+ patch series" when the operator
  ratified the 1.3.0 renumbering, 2026-08 — contents unchanged, all items remain deferred
  out of v1.3.0. UI and flow tweaks from lived use; expected one or two releases.
  Currently queued: the **print timestamp toggle**
  (None / Absolute / Relative — None shipped as Session N's fixed default), the
  **link-appendix mode toggle** (appendix / inline URLs / none — appendix shipped as the
  fixed default), and the **summary-placement toggle** (body-level control over where the
  conversation summary appears — metadata-only shipped as Session O's fixed resolution:
  frontmatter when present, so Copy omits it and Print keeps showing it), plus whatever
  lived use surfaces.
- Embedded-HTML display policy — the viewer and print strip disallowed HTML tags from
  displayed text (a literal `<details>` typed in a message vanishes from display), losing
  characters the author wrote; evaluate escape-to-visible-text as the display default, and
  separately whether closed, well-formed details blocks deserve real collapsible
  rendering — a sanitiser-allowlist decision requiring its own written brief.
- Launcher message polish (POSIX) — on the missing-venv-module path, Python's raw
  ensurepip error prints before the friendly message; consider suppressing or summarising
  the underlying stderr so the friendly message stands alone.
- Launcher detection polish (Windows) — when the `py` launcher's default is pinned to an
  older Python, installing a newer one doesn't change what `py -3` reports, so the too-old
  message's advice can appear not to work; consider probing the launcher's full list
  (`py -0p`) or versioned qualifiers before declaring "too old", or mentioning the pin
  possibility in the message.

### 1.4+ (unscheduled)

Order between the two larger items below is undecided until development resumes; the
current lean is portable tag state first.

- Artifact + file-tools extraction. Blocked behind a manufactured test corpus: deliberately
  create artifacts of every type — edit histories, rewrites, branch divergence — export, and
  inspect the designed corpus before designing the feature; combine with the file-tools scan
  (`create_file`/`str_replace` bodies) since the two may be one "recover authored content"
  feature. Do not build against the current export's biased sample.
- Portable, mergeable tag state (full profile system). The tag database is the app's only
  irreplaceable state; the export is re-downloadable, the triage work is not. The hard part
  is reconciliation semantics for one user across multiple machines (last-write-wins,
  surfaced conflicts, rollback); needs its own discovery/design pass before any code.
