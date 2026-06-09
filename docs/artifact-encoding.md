# Artifact encoding in the Claude.ai data export

Empirical reference for how Claude.ai artifacts appear inside the data-export ZIP, and whether
their content is recoverable. Companion to `docs/export-schema.md`; produced by the v1.1
Session D discovery pass. Everything here was determined from a single real export (June 2026,
single account, 190 conversations) — these are **one-export observations, not format
guarantees**. The export carries no version markers, so any of this may shift silently.

All examples are synthetic, written from scratch to show the shape. No content, titles, or
identifiers from the reference export are reproduced here.

The scanner that produced the aggregate numbers is committed at `tools/scan_artifacts.py`
(stdlib-only; prints aggregates, never content). Re-run it against any export ZIP:

```
python tools/scan_artifacts.py path/to/export.zip
```

---

## The observed encoding

An artifact operation is a `tool_use` content block with `name == "artifacts"`, immediately
followed (in the same message's `content` list) by its `tool_result`. The general `tool_use` /
`tool_result` field inventory is in `docs/export-schema.md`; the artifact-specific facts:

- **Both block-level ids are `null`.** The `tool_use.id` and the `tool_result.tool_use_id` were
  `null` on every observed artifact call — unlike, say, `web_search` calls, which carry
  `toolu_…` ids. Pairing must therefore be **positional**: the `tool_result` directly follows
  its `tool_use` in the same message (held for 34 of 34 observed calls).
- **The `tool_result` is a bare acknowledgement.** Every observed result was
  `is_error: false` with a single inner text block whose text is the two-character string
  `"OK"`. The result carries no artifact content and nothing worth rendering.

### The `input` payload

```jsonc
{
  "command":      "create",                  // see "Command grammar" below
  "id":           "synthetic-artifact-1",    // model-chosen slug, stable per artifact
  "type":         "text/markdown",           // MIME-style artifact type
  "language":     null,                      // populated for code artifacts (not observed)
  "title":        "Synthetic artifact title",
  "content":      "# Synthetic\n\nFull artifact body…",
  "source":       "c",                       // constant in observed data; semantics unknown
  "md_citations": [ /* citation spans — see below */ ],
  "version_uuid": "00000000-0000-0000-0000-0000000000e2"
}
```

Field semantics, as observed:

| Field          | Observed shape                  | Notes                                            |
|----------------|---------------------------------|--------------------------------------------------|
| `command`      | `"create"` (only value seen)    | The operation; grammar below.                    |
| `id`           | string slug                     | Identifies the artifact across commands.         |
| `type`         | `"text/markdown"` (only value)  | Other types (code/html/react/svg) not observed.  |
| `language`     | `null` on all calls             | Presumably a language slug on code artifacts.    |
| `title`        | string, 15–125 chars observed   | Display title.                                   |
| `content`      | string, full body               | The artifact itself. 5,573–56,158 chars observed.|
| `source`       | the literal string `"c"`        | Constant across all calls; meaning unknown. The earlier schema doc guessed `null`; that was wrong for this export. |
| `md_citations` | list (non-empty on all calls)   | Citation spans into `content`; see below.        |
| `version_uuid` | UUID string, distinct per call  | One per operation — a version marker, not an artifact id. |

All nine fields were present on **every** observed call regardless of content size.

### Command grammar

Only **`create`** was observed: full `content`, plus all metadata fields. One `create` per
artifact id; no artifact in the reference export was ever edited afterwards.

**`update` and `rewrite` are expected but unobserved.** The claude.ai client edits artifacts
with update (targeted old-string/new-string replacement) and rewrite (full replacement)
operations, so an export from an account that edits artifacts would presumably contain them —
but this export contains zero, so their exported shape (field names, whether `update` carries
`old_str`/`new_str`, whether `content` is present on `rewrite`) is **unverified inference**.
Anything consuming these commands must treat their shape as unknown until an export containing
them has been scanned — preserve-and-flag, per house rules. (The `old_str`/`new_str` strings
that do appear in the export belong exclusively to the separate `str_replace` *file* tool, not
to artifacts.)

Also checked: the export contains no legacy artifact encoding (no `<antArtifact>` tags, no
`application/vnd.ant.*` types anywhere in `conversations.json`).

### `md_citations`

Citation spans linking ranges of `content` to web sources — present and non-empty on every
observed call because every observed artifact was a research-style markdown report. List
lengths ranged from 2 to 265 items (3,084 items total). Each item:

```jsonc
{
  "uuid":             "00000000-0000-0000-0000-0000000000f1",
  "title":            "Synthetic source page title",
  "url":              "https://example.invalid/page",
  "start_index":      120,     // character offsets into content
  "end_index":        185,     // end can equal len(content) — exclusive bound
  "origin_tool_name": "web_search",   // or "web_fetch"
  "metadata":         { "icon_url": null, "preview_title": null, "source": "…",
                        "type": "…", "uuid": "…" },
  "sources": [
    { "content_body": null, "icon_url": null, "resource_type": null, "source": "…",
      "subtitles": null, "title": "…", "url": "https://example.invalid/page", "uuid": "…" }
  ]
}
```

(Values shown as `"…"`/`null` were not characterised further; key sets were uniform across all
3,084 items.) A renderer can ignore these; an extractor should preserve them — they are the
only link between report text and its sources.

---

## Aggregate findings (reference export, June 2026)

| Measure | Value |
|---|---|
| Conversations / messages | 190 / 2,527 |
| Conversations with branch points | 23 |
| Artifact `tool_use` calls | **34**, in 28 conversations |
| Command distribution | `create`: 34 — no `update`, no `rewrite` |
| Artifact types | `text/markdown`: 34 (no code/html/react/svg/mermaid) |
| Artifacts per conversation | 1 × 24 convs, 2 × 3 convs, 4 × 1 conv |
| Multi-command artifact ids | 0 (every history is a single `create`) |
| Branch position | 32 entirely on the canonical path; 2 entirely on abandoned branches |
| `tool_result` pairing | 34/34 positional, same message, `is_error: false`, text `"OK"` |
| Content lengths (chars) | min 5,573 · median 20,446 · p90 45,068 · max 56,158 |
| Citation spans | 3,084 (`web_search` 2,922 · `web_fetch` 162), all within content bounds |

Worth noting for future planning: this account's document-like deliverables increasingly flow
through the **file tools** instead of artifacts — 113 `create_file` and 120 `str_replace` calls
versus 34 artifact calls. Those inputs carry full bodies too (`file_text`, `old_str`/`new_str`),
so the same recovery approach would apply, but they are a separate mechanism and out of scope
here.

---

## Truncation findings

**Verdict: no evidence of truncation.** What was checked, and what each check found:

1. **Code-fence balance** — all 34 `content` strings contain an even number of ` ``` ` fences;
   none ends inside an open fence.
2. **Explicit markers** — no `[truncated`-style marker strings appear in any artifact content.
3. **Length pile-ups** — all 34 content lengths are distinct; no clustering at a shared maximum
   (the signature a hard cap would leave). The largest call is 56,158 chars.
4. **Field presence by size** — all nine input fields are present on every call; nothing is
   dropped on large calls relative to small ones.
5. **Citation-offset bounds** — the strongest check: all 3,084 `md_citations` offsets fall
   within their call's `content` length, and on several calls the maximum `end_index` equals
   the content length exactly — citations reach the final character, which truncated content
   would not satisfy.
6. **Result status** — every paired `tool_result` is a non-error `"OK"`.

Checks that could not be exercised on this export: dangling-JSON and unclosed-HTML/SVG checks
(no artifacts of those types), and `old_str` truncation on `update` calls (none exist). The
scanner implements the per-type checks, so they run automatically if a future export contains
those types.

---

## Reconstruction results

Method: a scratch replay engine (create = snapshot; rewrite = replace snapshot; update = apply
`old_str` → `new_str` exactly once, flagging zero or multiple matches), with each artifact's
calls ordered by parent-tree depth and verified to lie on a single root-to-leaf chain.

| Sample category (from the brief) | Samples in export | Result |
|---|---|---|
| (a) create-only artifact | 34 | **34/34 reconstruct cleanly** — final state is literally the `create` call's `content`; fences balanced; lengths match |
| (b) `update` chain (old_str/new_str replay) | 0 | **Untestable** — no update calls exist in this export |
| (c) `rewrite` chain | 0 | **Untestable** — no rewrite calls exist in this export |
| (d) edit history spanning a branch point | 0 | **Untestable** — every history is a single call on a single branch |

No replay anomalies of any kind. The honest summary: for the data that exists, reconstruction
is trivial; the hard cases (replay fidelity, branch-divergent histories) remain theoretical
because the export contains no instances of them.

---

## Branch semantics

The problem: when a conversation branches (user edits or regenerates), an artifact edited on
both sides of the branch point has **no single final state** — "final" is only well-defined
per root-to-leaf path. The reference export contains no such history, but it does contain the
adjacent case: **2 of 34 artifacts exist only on abandoned branches**, so a viewer that renders
only the canonical path never encounters their `create` calls at all.

Candidate resolution rules a future implementation could adopt (laid out, deliberately not
chosen here — this is a v1.2 planning decision):

1. **Resolve along the rendered path.** An artifact's state is the result of replaying only the
   commands on the currently displayed root-to-leaf path. Consistent with what the conversation
   view shows; artifacts on other branches appear when the user switches branches.
2. **Canonical path only.** Simplest; ignores off-canonical artifacts entirely (would hide 2 of
   34 in the reference export).
3. **Union across branches.** Reconstruct one final state per artifact id per branch leaf and
   surface all of them (labelled by branch). Most complete; needs deduplication and UI
   vocabulary for "versions" that the viewer currently lacks.

---

## Recommendation

**Feasible.** Artifact content is present in full in the export, and for every artifact
observed so far, recovery is a single read: the `create` call's `content` field, with `title`,
`type`, and `language` available for naming a downloaded file. Nothing needs replaying yet.

Proposed minimal scope for a future implementation session (input to v1.2 planning, not a
commitment):

- Reconstruct final state **along the rendered path** (rule 1 above is the natural default, but
  the rule choice stays open): `create`/`rewrite` as snapshots, `update` as exact-match
  `old_str` replacement — preserve-and-flag on unknown commands, zero/multiple matches, or
  missing fields, falling back to the last good snapshot with a visible warning.
- Render the real content in the placeholder cards, plus a per-artifact download named from
  `title`/`type` — and with that, the v1.0-parity "download artifacts as ZIP" item becomes
  implementable.
- Out of minimal scope: artifacts on non-rendered branches (pending the branch-semantics
  decision), `md_citations` rendering, and any handling of the file-tool
  (`create_file`/`str_replace`) pipeline — the last of these is a separate mechanism worth its
  own discovery pass if it grows.
