# Export Categories Design — v1.3.1 and v1.4.0

**Status**: normative. Ratified by the operator 2026-10-27 through the chat channel;
amended only through the same channel. Implementation briefs cite this record as the spec
and add mechanics only. Decisions marked *provisional* rest on a small observed sample and
are expected to be revisited when more data is seen; everything else is locked.

**Relationship to other records**: `docs/format-support-design.md` stays normative for the
container formats it covers; this record **amends it** (§9 below, A5–A7) and adds three
categories it did not know about. `docs/export-schema.md` describes what the data *is*;
this record describes what the viewer *does* with it. The discovery evidence is the
verification table (§2), produced by an aggregate-only chat-side scan of three real
exports (legacy, early-October current, late-October current); the implementation session
reproduces it with the committed scanner.

## 1. Scope and principles

Since v1.3.0 shipped, Anthropic's export gained three category files beside the four the
viewer knows: `frames` (published artifacts), `artifact_data` (data saved by artifacts),
and `claude_code_cloud` (Claude Code scheduled tasks and their runs). The v1.3.0 sniffer
refuses any file it does not recognise, so **uploading a current export exactly as the
README instructs fails today**. Separately, the legacy conversation-memory summary still
ships in current exports but stopped updating when accounts moved to memory files.

Two versions, one design:

- **v1.3.1 (patch)** — the viewer stops breaking: unrecognised files are skipped instead
  of fatal (A6), the three new categories are recognised and kept but not displayed, the
  banner generalises, the legacy memory is de-emphasised with a hide setting, the
  `document` content block gets a typed rendering, legacy archives' login history loads,
  the manifest links page ships, and the ratified 1.3.1 items (A5 size-collapse; the real
  export out of the tree) land.
- **v1.4.0 (minor)** — the three categories are displayed: an Artifacts page with
  sandboxed previews, artifact data, and conversation cross-links; a Scheduled tasks page.
  The plan's previous 1.4+ items move to **1.5+**, unchanged.

Principles, all continuations: one internal model; preserve and flag; **no runtime network
calls, and no network reachable from anything the viewer renders**; any subset loads;
content decides, filenames never do; nothing real in the tree.

## 2. Verification table (targets, not premises)

Observed in the operator's real exports, aggregate-only. The implementation session
reproduces each row with the extended `tools/scan_export_schema.py` against
`VIEWER_REAL_EXPORT_DIR` and records confirm/diverge. **Divergence is a finding, not a
failure** — record it, and stop only where a divergence contradicts a decision below.

| # | Target | Observed |
|---|---|---|
| V1 | Manifest category enum | `light_metadata`, `projects`, `memories`, `frames`, `artifact_data`, `claude_code_cloud`, `conversations`; `version` `1.0`; `total_files` equals the list length |
| V2 | Categories present vary by account and date | early-October set: 6 files incl. `frames`; late-October set: 8 files incl. all three new categories |
| V3 | `frames` layout | `artifacts/<uuid>/artifact.json` per artifact; versions under `artifacts/<uuid>/versions/` as either a single `<version-id>.html` **or** a `<version-id>.files.json` beside a `<version-id>/` directory of files |
| V4 | `artifact.json` keys | `id` (uuid, equals the directory name), `kind`, `visibility`, `owner_account` (uuid), `updated_at`, `active_version`, `versions[]` of `{id, title, description, created_at}` |
| V5 | `kind` values | `artifact`, `page`; `visibility` value `private` only |
| V6 | Version id shape | `<unix-seconds>-<4 hex>`; the seconds equal `created_at` exactly |
| V7 | Version ordering | the versions list is **not** guaranteed chronological, and `active_version` is **not** guaranteed to be the newest — but it is always in the list |
| V8 | `.files.json` | `{files: [{original_path, exported_as, sha256, size_bytes, mime_type}]}`; `exported_as` equals `original_path`; relative paths, depth 0–2, no leading `/`; exactly one `text/html` entry, at depth 0; every listed file present with matching SHA-256 and size; MIME values seen: `text/html`, `text/markdown`, `text/css`, `text/javascript`, `font/woff2`, `application/json` |
| V9 | Multi-file entry HTML | large bundled JS and CSS beside it; Markdown data files carry the authored content |
| V10 | `page`-kind artifacts | **no version files**; instead a root-level Markdown file (the content) and a root-level JSON comments file: a list of threads `{resolved, tab, quoted_text, comments: [{body, created_at}]}` |
| V11 | Single-HTML versions | a complete page with doctype; may reference external fonts; no scripts in the observed one |
| V12 | `artifact_data` layout | `artifacts/<uuid>/data/<name>.json` → `{documents: [{collection, id, created_at, updated_at, data}]}`; `data` is an **open set** of user-defined keys; the uuid matches an artifact in `frames` |
| V13 | Capability config in a multi-file artifact | a small JSON `{v, source: "db", db: {meta, collections[]}}` among the version files |
| V14 | `claude_code_cloud` layout | `claude_code_cloud/routines.json` and `claude_code_cloud/routine_runs.json`, each `{claude_code_cloud: [...]}` |
| V15 | Routine record | `routine_id` (`trig_` prefix), `agent_id` (null), `name`, `prompt`, `prompt_withheld` (bool), `enabled` (bool), `schedule {cron, run_once_at}` (exactly one non-null), `created_at`, `updated_at`, `ended_at` (null) |
| V16 | Run record | `routine_id`, `scheduled_for`, `started_at`, `finished_at`, `status` (`succeeded` only observed), `session_id` (`cse_` prefix); every run's routine exists; most routines have no runs |
| V17 | Conversation → artifact links | an `Artifact` tool result carries `structured_content.artifact_id` and `structured_content.url`; Docs connector tool inputs carry `input.container.id`; artifact ids also appear in tool-result text; two of three artifacts are referenced (*provisional*: small sample) |
| V18 | New content block | `type: document`, human-sent, keys `file_uuid`, `title` (occasionally absent), `flags`, `start_timestamp`, `stop_timestamp` |
| V19 | Other new conversation keys | block-level `title`, `file_uuid`, `meta.tool_references`; many new open-set tool `input` keys; attachment `file_type` `text/vtt` |
| V20 | Legacy memory frozen | `conversations_memory` is byte-identical across the two October exports while `memory_files` changed between them; it differs from the August legacy archive's |
| V21 | Legacy archive login history | the August legacy single ZIP carries `login_history.json` at the root, same `{login_events: [...]}` shape as the current light_metadata file |

## 3. Detection and the unrecognised-file amendment (A6)

New content signatures, added to record §2's table of `format-support-design.md`:

| Supplied file | Identified as | Signature |
|---|---|---|
| ZIP whose members all sit under `artifacts/<uuid>/` and include at least one `artifacts/<uuid>/artifact.json` | `frames` | Per-artifact descriptor |
| ZIP whose members all match `artifacts/<uuid>/data/<name>.json` | `artifact_data` | Data directory, no descriptor |
| ZIP whose members all sit under `claude_code_cloud/` | `claude_code_cloud` | Category-named directory |

**A6 — unrecognised files are skipped, not fatal.** A submitted file that matches no
signature (and is not a manifest) is skipped with the D1 notice; the rest of the
submission loads. If *no* file in a submission is recognised, the existing error stands.
Skipped files are never stored. The mixed legacy/current refusal is unchanged. Rationale:
the export has grown a category three times in four months; a viewer that refuses the
whole upload on the next one breaks every user on the day it ships.

## 4. Banner and categories (A7)

**A7 — the degraded-view banner generalises.** The four original categories are the
**core** set. Without a manifest, the banner appears only when a core category is missing,
and its lead counts against 4 as today. With a manifest, expected categories are the
manifest's distinct `category` values, the lead counts against that number, and every
missing listed category gets its line — including the three new ones (C1 extension, D3).
Categories the viewer does not know but the manifest lists are reported by their raw name
(D3). The expected-vs-supplied file line is unchanged.

## 5. v1.3.1 behaviours

1. **New categories kept, not displayed.** Recognised `frames`, `artifact_data`, and
   `claude_code_cloud` files are stored in the set directory, counted in the set summary
   and fingerprint like any category, and parsed far enough to validate their structure
   (a structural failure skips the file with the D1-style notice D2 rather than failing the
   set). The Home page and Help say they are kept but not yet displayed (D4).
2. **Legacy memory de-emphasis** (§7).
3. **`document` content block** — a typed block (`file_uuid`, `title`, flags, timestamps
   preserved) replacing its `UnknownBlock` fallback; renders as a labelled file line (D6);
   the conversation export emits D6's export line instead of the unrecognised-block line.
4. **Legacy login history** — a legacy archive containing `login_history.json` loads it
   into the same model slot as the current format; the Account page shows it. Absent:
   unchanged (C4 empty state).
5. **Manifest links page** (§8).
6. **A5** (size-collapse for display-content bodies) and **the real export out of the
   tree**, both as ratified in the 1.3.1 draft brief; the draft's §A and §C copy carry into
   this record's appendix unchanged (D11, D12).
7. **Launcher polish** — both queued items (POSIX: Python's raw ensurepip error no longer
   prints ahead of the friendly message; Windows: the too-old check probes the `py`
   launcher's full list before declaring "too old", and the message mentions the pin
   possibility). Copy for changed messages in D13.

## 6. v1.4.0 — published artifacts

### 6.1 Model

- `Artifact`: id, kind (raw string; unknown values preserved and flagged), visibility
  (raw), owner account, updated_at, active version id, versions, and — for `page` kind —
  the root content document and comment threads.
- `ArtifactVersion`: id, title, description, created_at, layout (`single_html`,
  `multi_file`, or `none`), and for `multi_file` the file list from `.files.json` with a
  per-file integrity result (present / size match / SHA-256 match).
- **Display order** is by `created_at`, newest first; the active version is marked and is
  the default view. The list order in `artifact.json` carries no meaning (V7).
- **Bytes stay out of the parse cache.** The model holds names and metadata; file bytes
  are read on demand from the stored set (or the in-memory set while inspecting). The
  pickle must not grow by artifact payload size — test-pinned, the same pattern as the
  signature drop.
- Preserve and flag: an `active_version` missing from the list, a version listed without
  files, files present but unlisted, size or hash mismatches, duplicate artifact ids
  across parts (kept once, flagged), unknown kinds, unreadable comment files.
- Owner-account verification mirrors the existing account-uuid check: a mismatch with
  `users.json` is logged, never fatal.

### 6.2 Artifacts pages

- **Nav entry `Artifacts`**, shown only when the loaded set includes `frames` (the Account
  page's rule). Placed after Memory.
- **List**: title (active version's), type, versions, updated; sort toggles use the
  canonical labels (Recently updated / Recently created / Title (A–Z)).
- **Detail**, top to bottom: title, type and visibility, description, version picker, the
  content area, files (multi-file only), comments (`page` only), saved data (when
  `artifact_data` has any), and "Mentioned in conversations".
- **Content area by layout**:
  - `single_html` and `multi_file`: the sandboxed preview (§6.3), with **View source**
    (the entry HTML through the existing highlighter, collapsed by A5's rule) and
    **Download**.
  - `page`: the root Markdown through the existing Markdown → sanitiser pipeline — no
    iframe, no new sanitiser surface.
  - `none` (no files): the D8 empty line.
- **Multi-file content files**: every `text/markdown` file is listed and can be expanded
  in place through the Markdown pipeline. For script-driven artifacts this is where the
  authored content lives (V9).
- **Downloads** are assembled in memory and write nothing server-side (the export
  feature's rule): a single-HTML version downloads as its `.html`; a multi-file version as
  a ZIP of its files at their original paths; a `page` as its `.md`.

### 6.3 The sandboxed preview — the security boundary

Ratified: **sandboxed, no scripts, no network.** Artifact HTML never passes through the
sanitiser; it is isolated instead. Outcomes, every one test-pinned:

1. The preview is an `iframe` whose `sandbox` attribute grants **neither `allow-scripts`
   nor `allow-same-origin`**.
2. Every artifact-file response carries a Content Security Policy (CSP) that permits no
   remote origin for any fetch directive and no scripts, and includes the `sandbox`
   directive, so the file is sandboxed **even when opened directly in a tab**. It also
   carries `X-Content-Type-Options: nosniff` and the file's recorded MIME type.
3. Files are served **only** by lookup in the version's recorded file list (or the single
   HTML). Request paths are matched against that list; they are never joined to a
   filesystem path. Traversal, absolute, encoded, and unlisted paths return 404.
4. The entry page's relative assets (CSS, fonts, images) load from the export; external
   references (web fonts, CDNs) are blocked by the CSP, never fetched.
5. If the entry HTML contains a `<script>` element, the D8 scripts notice shows above the
   preview. If it references any `http(s)` resource, the D8 external-resources notice
   shows.
6. Nothing in the preview can read the app's cookies, call its routes with credentials, or
   navigate the top window.

Verification must name the layer: header and attribute tests in the suite, plus a
**driven-browser pass** that confirms relative assets render, an external font request
never leaves the browser, and an inline script does not execute. If a stated outcome
cannot be met with server-rendered HTML and response headers alone, that is a hard stop.

### 6.4 Artifact data

Shown on its artifact's detail page under **Saved data**, grouped by collection, each
document collapsed with its id and timestamps as the summary and its `data` as a
formatted JSON view (open set — no field is interpreted). Data whose artifact is not in the
set is preserved and listed on the Artifacts page under the D8 orphans heading.

### 6.5 Conversation cross-links (*provisional*)

An artifact is "mentioned" by a conversation when its id appears in a tool block's
`input` or `structured_content` (V17). The detail page lists those conversations; the
reading view's tool card gains an **Open in Artifacts** link when the referenced artifact
is in the loaded set. Text-only mentions inside tool-result prose are not used for linking.

### 6.6 Search

Two new full-text search (FTS) kinds, the row-count invariant moved in lockstep: `artifact` (one row per
artifact: active version's title as title; body = description, the entry HTML's visible
text — script and style content excluded, stdlib parser only — or the `page` Markdown, plus
multi-file Markdown files) and `scheduled_task` (name as title, prompt as body). Hits link
to the artifact page or the scheduled-task row anchor.

## 7. Legacy memory (v1.3.1)

Trigger on the data, never on dates: when the loaded set contains **one or more memory
files**, the conversation-memory summary is **legacy** (V20 is the evidence). Otherwise it
remains the export's memory and renders exactly as today.

When legacy:
- The Memory page orders groups **Memory files, Project memories, Legacy memory**. The
  legacy group is collapsed by default, headed and annotated per D5.
- Search hits on it carry the D5 label.
- **Hide setting (operator request).** A `Hide legacy memory` control on the Memory page,
  **off by default**. On: the legacy group and its search hits are hidden whenever the set
  is in the legacy state. It never applies to exports without memory files — those always
  show the summary as their memory, so uploading an older archive brings it back
  automatically. Turning it off restores the de-emphasised view. Persisted as a
  first-party, local, HttpOnly cookie (`viewer_legacy_memory`), disclosed in Help in the
  Appearance pattern.
- **Inspect mode ignores the setting** and decides per inspected export: memory files
  present → de-emphasised; absent → shown as the memory. No control renders while
  inspecting.

## 8. Manifest links page (v1.3.1)

For users who receive only the manifest and don't know what to do with it. A page at
`/manifest`, linked from the Upload page and Help, not in the nav:

- A single-file picker; the manifest is **parsed in memory and never stored** — no set
  write, no history row, no cache change (the inspect no-trace snapshot test, applied).
- It shows the export creation date, the expiry warning, and a table of each listed file:
  filename, category, and a **Download** link to its `export_url`. The browser follows the
  link; **the viewer never fetches it**. Links render only for `https` URLs; anything else
  displays as text with no anchor. Links carry `rel="noopener noreferrer"`.
- Record §2 of `format-support-design.md` is amended accordingly (A8): the manifest's links
  may be *displayed to the user* on this page; the viewer still never dereferences them.

## 9. Amendments to `format-support-design.md` (verbatim)

- **A5** — as ratified in the 1.3.1 draft brief (D11).
- **A6 — append to §2:** `Unrecognised files are skipped, not fatal. A submitted file
  that matches no signature and is not a manifest is skipped with a notice and never
  stored; the rest of the submission loads. A submission with no recognised file is
  refused as before. Ratified 2026-10 after the export gained three categories the v1.3.0
  sniffer refused. New category signatures live in docs/export-categories-design.md §3.`
- **A7 — append to §4:** `The degraded-view banner counts the four core categories when
  no manifest is supplied; with a manifest it counts the manifest's distinct categories
  and names every missing one, including categories this version doesn't recognise. See
  docs/export-categories-design.md §4.`
- **A8 — append to §2, after the export_url paragraph:** `Exception, ratified 2026-10: the
  manifest links page (docs/export-categories-design.md §8) displays the links to the user
  as https anchors their browser follows. The viewer itself still never fetches them, and
  the page stores nothing.`

## 10. Combinations and extremes (walked on purpose)

| Case | Behaviour |
|---|---|
| `frames` alone, or any new category alone | Loads; core categories missing → banner; nav shows what the data supports |
| `artifact_data` without `frames` | Loads; all data listed as orphans |
| `claude_code_cloud` with runs for an absent routine | Runs preserved under the D9 orphan heading |
| `frames-000` + `frames-001` | The §3 multi-part merge (*provisional*); duplicate artifact id kept once, flagged |
| A future unknown category | Skipped with D1; listed by raw name in the banner when a manifest names it |
| Empty `frames` ZIP | No signature → skipped with D1 |
| Artifact with 0 versions / active version missing | Preserved, flagged; detail page shows D8's empty line |
| Hundreds of versions | Picker is a plain list; nothing loads until selected |
| Entry HTML or bundle of many MB | List and detail pages never inline bodies; the iframe loads on demand; View source collapses per A5 |
| Hash or size mismatch | Shown in the files table (D8); the file is still served |
| `page` with no comments file / no content file | The section is omitted / D8 empty line |
| Exports without memory files | Memory page unchanged from v1.3.0; hide setting inert |
| Inspecting while the hide setting is on | Setting ignored (§7) |

## 11. Considered and deferred

- **Running artifact scripts** (an opt-in scripted preview) — rejected for now; revisit
  only with its own design pass.
- **Rendering `page` comments anchored in the text** — they render as a list with their
  quoted text; anchoring is a later candidate.
- **Interpreting `artifact_data` fields** — never; open set.
- **Linking scheduled-task sessions to anything** — `session_id` displays as text; the
  export carries no session content.
- The previous 1.4+ items (portable tag state; streaming parse; reconstruction of
  tool-call artifacts — now partly superseded by `frames`, re-scoped in its own pass)
  become **1.5+**.

## Appendix — user-facing copy (verbatim)

No copy holes: implementation uses these exactly. Explicit drafting latitude is granted
**only** for new Help sections and README feature bullets, in the established voice, every
drafted passage listed in the phase report for operator review.

**D1 — skipped-file notice (Upload and Inspect).** `{filename} isn't a file this version
of the viewer recognises, so it was skipped. The rest of your export loaded.`

**D2 — invalid category file.** `{filename} looks like a {category} file but couldn't be
read, so it was skipped. The rest of your export loaded.`

**D3 — banner lines (C1 extension).** Lead: `Loaded {n} of {m} export categories.`
New lines:
- `No frames file — published artifacts are unavailable.`
- `No artifact data file — data saved by published artifacts is unavailable.`
- `No Claude Code cloud file — scheduled tasks and their run history are unavailable.`
- Unknown listed category: `No {category} file — this version of the viewer doesn't use it yet.`

**D4 — v1.3.1 kept-not-displayed (Home line and Help, "Loading an export").** `Current
exports can also include files for published artifacts, artifact data, and Claude Code
scheduled tasks. This version accepts them with the rest of your export and keeps them,
but doesn't display them yet.` (Removed in v1.4.0 for the categories it displays.)

**D5 — legacy memory.**
- Group heading: `Legacy memory (no longer updated)`
- Note under the heading: `This is the summary from Claude's previous memory system. It
  stopped updating when your account moved to memory files, so it may not match them.`
- Control label: `Hide legacy memory`
- Search hit label: `Legacy memory`
- Help (Memory section): `When your export includes memory files, the older memory
  summary is shown last, collapsed, and marked as no longer updated. To hide it from the
  Memory page and search, turn on Hide legacy memory. Exports without memory files always
  show their summary, and Inspect mode decides for each export it opens.`
- Help (Appearance section): `The Hide legacy memory choice is remembered the same way,
  in a cookie named viewer_legacy_memory. Nothing is sent anywhere.`

**D6 — document block.** Card label `Document`; line `{title}` (`Untitled document` when
absent); note `The file itself isn't included in the export.` Export line:
`> **[Document: {title}]**`.

**D7 — manifest links page.**
- Page title: `Download links`
- Intro: `Open the manifest file from your export email to see its download links.
  Download each file, then upload them all together on the Upload page.`
- Picker label `Manifest file`; button `Show download links`
- Created line: `Export created {date}.`
- Warning: `These links are personal and expire about 24 hours after the export was
  created. Don't share them. The viewer doesn't download anything itself and doesn't keep
  your manifest.`
- Table headers `File`, `Category`, `Download`; link text `Download`
- Non-https entry: `(link not shown)`
- Error: `That file isn't an export manifest.`
- Upload page pointer: `Only have the manifest file? Get your download links.`
- README "Getting your export" (replaces C8): `In Claude (claude.ai or the desktop app),
  click your initials in the lower-left corner, open **Settings**, go to **Privacy**, and
  click **Export data**. Anthropic emails you a download link — it expires after 24 hours,
  so download promptly. Current exports arrive as a small manifest file listing several
  ZIP files (conversations, projects, memories, account metadata, and — if you use them —
  published artifacts and Claude Code scheduled tasks). The viewer's **Download links**
  page turns the manifest into one-click links. Download them all and upload them to the
  viewer together, exactly as downloaded — there's no need to unzip anything. Older
  single-ZIP exports load exactly as before. The in-app **Help** page covers the rest,
  from what the export contains to backups and troubleshooting.`

**D8 — Artifacts.**
- Nav `Artifacts`; page heading `Published artifacts`
- List headers `Title`, `Type`, `Versions`, `Updated`
- Type labels: `artifact` → `Artifact`, `page` → `Document`, other → the raw value
- Visibility labels: `private` → `Private`, other → the raw value
- Version picker label `Version`; marker `Active version`; line `Created {date}`
- Buttons `View source`, `Download`
- Preview heading `Preview`
- Scripts notice: `This page uses scripts, which the viewer doesn't run, so the preview
  may be incomplete. Its content files are listed below.`
- External-resources notice: `Fonts and other resources from the web are blocked, so the
  preview may look a little different from the original.`
- Files heading `Files`; headers `Path`, `Type`, `Size`, `Check`; values `OK`,
  `Doesn't match the export's record`, `Missing from the export`
- Content files heading `Content files`
- Empty content: `This version has no files in the export.`
- Comments heading `Comments`; badge `Resolved`; quoted line `On “{quoted_text}”`
- Saved data heading `Saved data`; group line `Collection: {name}`
- Mentions heading `Mentioned in conversations`; empty `Not mentioned in any conversation
  in this export.`
- Tool-card link `Open in Artifacts`
- Orphans heading `Saved data without a published artifact`

**D9 — Scheduled tasks.**
- Nav `Scheduled tasks`; headings `Scheduled tasks`, `Run history`
- Task headers `Name`, `Schedule`, `Enabled`, `Created`, `Last run`
- Schedule cells: cron → `Repeats: {cron}`; one-off → `Once: {datetime}`
- Enabled values `Yes`, `No`; no runs `Never`
- Prompt summary `Prompt`; withheld `The prompt was withheld from the export.`
- Run headers `Scheduled for`, `Started`, `Finished`, `Status`, `Session`
- Orphan runs heading `Runs for tasks not in this export`
- Help intro sentence: `Claude Code scheduled tasks (called routines in the export
  files) are listed with their prompts, schedules, and run history.`

**D10 — release notes.**
`docs/briefs/v1.3.1-release-notes.md`:
`## v1.3.1 — current exports load again`
`- Exports with files this version doesn't recognise now load; unrecognised files are
skipped with a notice instead of stopping the upload.`
`- Published-artifact, artifact-data, and Claude Code files are accepted and kept (shown
from v1.4.0).`
`- The old memory summary is marked as no longer updated when your export has memory
files, and can be hidden.`
`- New Download links page turns your manifest into one-click downloads.`
`- Attached documents in conversations now show by name; older single-ZIP exports show
their login history.`
`- Large tool results start collapsed with a line count.`
`- The real-data test hook takes an external path (VIEWER_REAL_EXPORT_DIR); the data/
development fallback is retired.`
`- Launcher messages are clearer on Linux/macOS and on Windows with several Pythons.`
`docs/briefs/v1.4.0-release-notes.md`:
`## v1.4.0 — published artifacts and scheduled tasks`
`- New Artifacts page: every published artifact with its versions, a safe preview (no
scripts, nothing loaded from the web), its files, comments, and saved data.`
`- Conversations link to the artifacts they published.`
`- New Scheduled tasks page for Claude Code routines and their run history.`
`- Artifacts and scheduled tasks are searchable.`

**D11 — A5 and its copy** carry from the 1.3.1 draft brief unchanged, reproduced verbatim
in the Session U brief's appendix (U1 teaser, U2 hand-test addendum, U3 amendment text).

**D12 — real export out of the tree.** Rationale of record: *real exports never live in
the tree, gitignored or not*; the stored set in `cache/active_export/` already provides
everything the `data/` copy did.

**D13 — launcher messages.** POSIX, replacing the venv-failure path's output (the raw
ensurepip stderr is suppressed): keep the existing friendly message verbatim, adding at
its end `(Python's own error message was hidden; run "python3 -m venv .venv" yourself to
see it.)`. Windows too-old message, appended sentence: `If you've installed a newer
Python and still see this, the py launcher may be pinned to an older version — run
"py -0p" to list what's installed.`
