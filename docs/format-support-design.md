# Format Support Design — v1.3.0

**Status**: normative. Ratified by the operator 2026-08-25 through the chat channel; amended
only through the same channel. Implementation briefs cite this record as the spec and add
mechanics only. Decisions marked *provisional* were approved as a starting shape and are
expected to be revisited against operator testing; everything else is locked.

**Relationship to other records**: `docs/export-schema.md` describes what the export data
*is*, for both container formats; this record describes what the viewer *does* with it.
The Session R reports (`docs/briefs/`) carry the discovery evidence and the codebase-review
findings this design responds to.

## 1. Scope and principles

v1.3.0 makes the viewer load both export container formats: the legacy single ZIP and the
current manifest-plus-category-ZIPs delivery, including the new memory system. Guiding
principles, all continuations of existing ones:

- **One internal model.** Both containers normalise into the same `Export` model at the
  existing parse→model seam. Everything downstream — render, search, tagging, export,
  print, inspect — stays container-agnostic.
- **Preserve and flag** continues to govern surprises. This record adds *deliberate,
  documented* exceptions where warranted (§6, signatures) — a recorded decision is not
  silent dropping.
- **No runtime network calls**, unchanged. In particular the app never dereferences a
  manifest's `export_url` links (§2).
- **Any subset loads.** The current format has no required file; missing categories
  degrade features visibly, never fatally (§3).

## 2. Container detection

Detection is by **content, never filename**. Filenames like `conversations-000.zip` may be
shown to the user but never decide parsing.

| Supplied file | Identified as | Signature |
|---|---|---|
| ZIP with root-level `users.json` + `memories.json` + `conversations.json` | Legacy export | The legacy required trio |
| ZIP whose only root member is `conversations.json` | Current conversations category | No sibling legacy files |
| ZIP with members matching `projects/<uuid>.json` | Current projects category | Per-project files in a subdirectory |
| ZIP with a member matching `memories/<uuid>.json` | Current memories category | Single dict-shaped file in a subdirectory |
| ZIP with root-level `users.json` + `login_history.json` | Current light_metadata category | The pair, without the legacy trio |
| `.json` that parses to an object containing `data_files` | Manifest | Used for expected-vs-supplied display only |

The manifest's `export_url` values are live signed links. The viewer **never fetches
them** — the manifest is metadata for the banner (§4) and nothing more — and the help page
carries a safety note telling users not to share or commit their manifest (Appendix, C2).

## 3. Loading and assembly

- A **sibling current-format loader** beside the legacy `_parse_zip` path, sharing the
  per-file parsers, normalising into `Export`. The legacy path is untouched.
- **Multi-part categories** (*provisional — no multi-part export has been observed*): all
  parts of a category are merged by concatenating their lists. A conversation UUID
  appearing in more than one part is preserved once and flagged with a `WARNING`
  (preserve-and-flag). The legacy batch-filename warning is updated to recognise the
  current `-NNN` part naming.
- **No required file.** Each missing category disables its features, disclosed by the
  degraded-view banner (§4):

| Missing | Consequence |
|---|---|
| conversations | Conversation browsing and search are empty |
| projects | Project pages and project names unavailable |
| memories | Memory page unavailable |
| light_metadata | Account page unavailable; account-UUID verification skipped |

## 4. Upload and inspect UX

- **One multi-file input** on both Upload and Inspect: accepts `.zip` files and a manifest
  `.json`, any subset, any order. Each file is sniffed per §2 and slotted into its
  category. Duplicate categories in one submission: last file wins, with a warning.
- **Atomic set swap**, generalising parse-before-replace: stage the supplied set, parse
  the set, and only on success swap it in as the active export.
- **Degraded-view banner** (*provisional as part of the upload shape*): a persistent
  banner whenever the active set is missing categories, using the copy in Appendix C1.
  When a manifest was supplied, the banner also states expected-vs-supplied counts.
- **Upload cap: 200 MB per file** (was: per upload). The constant is promoted to a
  documented configuration knob — environment variable `VIEWER_MAX_UPLOAD_MB`, read at
  startup, default 200 (rename to match an existing configuration convention if one is
  found, disclosed as a judgment call). Both existing enforcement sides (client hint and
  server rejection) are preserved. Rationale, recorded: the cap is a parse-memory
  guardrail, not a format limit; the current format's per-category delivery and
  Anthropic's own part-splitting make the default generous, and power users with large
  archives can raise it locally without a release. Incremental/streaming parse is queued
  under 1.4+ as the long-term answer (§11).
- **Inspect mode** uses the same picker and detection, parses the set entirely in memory,
  and the no-trace property (zero disk artifacts, byte-identical `cache/` snapshots)
  extends to sets and remains test-pinned.

## 5. Storage, cache, and history

- The active export becomes a **set directory**, `cache/active_export/`, holding the
  supplied files as received. A legacy ZIP is simply a set of one — one code path. An
  existing `cache/active_export.zip` from earlier versions is still picked up by the
  startup load order (migration by tolerance, not conversion).
- **Set fingerprint**: a set of one keeps the plain SHA-256 of the file — existing history
  rows and startup dedup remain continuous. A set of many is fingerprinted as the SHA-256
  over the sorted per-file digests.
- The parse-cache key swaps its single zip digest for the set fingerprint (parser-module
  digest unchanged).
- **Upload history** records one row per loaded set; the filename column summarises the
  set (e.g. `3 files: conversations, projects, memories`); headline counts unchanged.

## 6. Model additions

New typed slots on known blocks (Session R Phase 2, Finding 3):

- `thinking`: `summaries` (list of strings, from the export's `{summary: …}` items) and
  `thinking_hidden` (bool).
- `text`: citations (URL plus offsets, preserved as given; offsets index the raw text).
- Tool blocks: `display_content` (shape preserved for the §9 renderers) and
  `meta.output_format_category`.
- `injected_prompt_block` becomes a typed block (prompt text plus its flags) rather than
  an unknown-type placeholder.
- Per-block `start_timestamp`/`stop_timestamp`: preserved on the model, not rendered.
- Knowledge-item `links` lists: preserved, not rendered (1.3.x candidate, §11).

**Signatures are deliberately dropped** — the decision of record: `signature` values are
opaque cryptographic attestation blobs (~20 MB aggregate in the reference archive) with
no human-readable content, no render use, and no export use; carrying them inflates the
parse cache for nothing. They are excluded from the model and the cache. This is
disclosed to users in the help entry at Appendix C3, which invites a GitHub issue from
anyone with a real use for them. `docs/export-schema.md` records the field itself.

## 7. Memory rendering

The Memory page renders whichever of three groups the archive supplies:

- **Conversation memory** — the opaque text blob, rendered as text.
- **Memory files** — grouped by whatever top-level path segments exist (**open set** —
  the viewer never enumerates an expected taxonomy). Each file is a collapsible section
  titled by its full path, showing its `updated_at`. Content handling: if the content
  opens with a `---` frontmatter fence, the fence block is displayed verbatim in a styled
  preformatted block and the body below it is rendered as sanitised Markdown; content
  without a fence renders entirely as sanitised Markdown. No YAML dependency is added —
  the locked stack stands; structured frontmatter presentation is a 1.3.x candidate
  (§11).
- **Project memories** — unchanged from legacy behaviour, dangling-project preservation
  included.

**Search**: FTS indexes all three — each memory file as its own row keyed by its path
(path as title), the conversation memory as one row, project memories as today. The FTS
row-count invariant is updated in lockstep with every added `kind` (the existing
tripwire).

## 8. Account page

A new **Account** page in the navigation, present whenever `users.json` is in the active
set (both formats — identity data is currently parsed and displayed nowhere, which the
show-your-data principle argues against regardless of format):

- **Account**: the identity fields from `users.json`.
- **Login history** (current format only; section shows the Appendix C4 empty-state line
  otherwise): a table, newest first — Timestamp (local-formatted), Method, IP address,
  Location, Browser & OS (condensed from the user-agent fields).

The nav entry is hidden when no `users.json` is loaded. The help page discloses the page
and what it shows.

## 9. Block rendering — Tier 1 (ratified in full)

Six behaviours, all shipping in v1.3.0:

1. **`injected_prompt_block`** renders as a labelled collapsible card ("Injected
   prompt"), collapsed by default.
2. **Thinking summaries** render as the collapsed thinking block's teaser text (first
   summary), with the full list visible at the top of the expanded block.
3. **`thinking_hidden`** blocks render normally with a small badge (Appendix C5) — the
   viewer shows everything in the user's export.
4. **Text-block citations** render as a "Sources" list beneath the block — deliberately
   *not* inline markers, because the offsets index the raw text and do not survive
   Markdown rendering. Inline anchoring is a 1.3.x candidate (§11).
5. **`display_content`**, when present, becomes the tool card's body: `text` as text;
   `code_block` through the existing highlighter; `table` built as an HTML table in the
   template from the data; `rich_link` as a link line; `json_block` and `rich_content`
   fall back to a collapsed preformatted JSON view. Bespoke rendering for the latter two
   is a 1.3.x candidate (§11).
6. **`output_format_category: md`** tool results render their text through the existing
   Markdown→sanitiser pipeline.

**Constraint, recorded as a hard stop**: none of the above requires a sanitiser-allowlist
change — tables and cards are template-built from data, and Markdown rendering reuses the
existing pipeline. If implementation discovers an allowlist change is needed anywhere,
that is hard-stop territory: stop and return to chat, per standing rules.

## 10. Conversation export and print

The Markdown serializer is **unchanged in v1.3.0**: new block metadata (summaries,
citations, display_content, timing) is not exported, and this record documents that as
the decision rather than an omission. `docs/export-design.md` remains the export
feature's normative record. Print inherits all §9 rendering through the deliberate
WYSIWYG design; the print CSS work itself belongs to the dark-mode session (light-token
reset, per the Session R Phase 2 report).

## 11. Considered and deferred

Queued to **1.3.x** (the patch series): inline citation anchoring; bespoke
`rich_content`/`json_block` rendering; knowledge `links` rendering; structured
frontmatter presentation for memory files; plus the pre-existing queue (toggles, launcher
polish, embedded-HTML policy, bare-URL autolinking).

Queued to **1.4+**: incremental/streaming parse ("large-archive posture") — removes the
parse-memory scaling that the upload cap currently guards, triggered if real users report
hitting limits.

Explicitly not built: any use of manifest `export_url` links; enumerated memory-file
taxonomy; serializer changes for new block metadata.

## Appendix — user-facing copy (verbatim)

Chat-authored words for every user-facing surface this design implies; implementation
uses these exactly (no copy holes).

**C1 — degraded-view banner.** Lead line: `Loaded {n} of 4 export categories.` Then one
line per missing category:

- `No conversations file — conversation browsing and search are empty.`
- `No projects file — project pages and project names are unavailable.`
- `No memories file — the Memory page is unavailable.`
- `No account file — account details and login history are unavailable.`

With a manifest supplied and a mismatch: `The manifest lists {m} files; {k} were
supplied.`

**C2 — manifest safety note (help, "Loading an export").** `Your manifest file contains
live, signed download links to your export. Treat it like the export itself: don't share
it, post it, or commit it anywhere public.`

**C3 — signature disclosure (help, "What the viewer doesn't keep").** `Thinking blocks in
current-format exports carry a verification signature — an opaque cryptographic blob with
no human-readable content. The viewer doesn't display, store, or cache signatures;
dropping them keeps the local cache smaller. A few other technical fields (per-block
timing, search-result link lists) are preserved but not yet displayed. If you have a use
for any of these, open an issue on GitHub.`

**C4 — Account page.** Nav label `Account`; headings `Account` and `Login history`; table
headers `Timestamp`, `Method`, `IP address`, `Location`, `Browser & OS`; empty state
`This export doesn't include login history.`

**C5 — thinking_hidden badge.** Badge text `Hidden in the app`; tooltip `This thinking
block is marked hidden in the Claude app; the viewer shows everything in your export.`

**C6 — upload cap (help, "Loading an export").** `Uploads are capped at 200 MB per file —
more than any export file seen so far. If yours ever exceeds it, set the
VIEWER_MAX_UPLOAD_MB environment variable before launching to raise the cap.`

**C7 — memory page.** Section headings `Conversation memory`, `Memory files`, `Project
memories`; per-file line `Updated {date}`.

**C8 — README "Getting your export" (replaces the single-ZIP paragraph).** `In Claude
(claude.ai or the desktop app), click your initials in the lower-left corner, open
**Settings**, go to **Privacy**, and click **Export data**. Anthropic emails you a
download link — it expires after 24 hours, so download promptly. Current exports arrive
as a small manifest file whose links download up to four ZIP files (conversations,
projects, memories, and account metadata); download them all and upload them to the
viewer together, exactly as downloaded — there's no need to unzip anything. Older
single-ZIP exports load exactly as before. The in-app **Help** page covers the rest, from
what the export contains to backups and troubleshooting.`

**C9 — help "Loading an export" (core replacement).** `Choose your export files and
upload them together — every ZIP from a current export plus, optionally, the manifest, or
a single legacy ZIP. You can load a partial set; a banner will tell you what's missing
and which features are affected.` (Followed by C2 and C6.)

**C10 — sources list heading (text-block citations).** `Sources`
