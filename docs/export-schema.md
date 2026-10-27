# Claude.ai data-export schema

Reference for the structure of the data export Anthropic delivers when a user requests their
Claude.ai data. All field shapes here were determined empirically from real exports of a single
account — a May 2026 export in the legacy format, an August 2026 export in the current
format, and (from v1.4.0) a later legacy archive plus two October 2026 current-format sets —
profiled with `tools/scan_export_schema.py` (aggregate-only output); they are not pulled from
public documentation. The October sets added three categories (`frames`, `artifact_data`,
`claude_code_cloud`); `docs/export-categories-design.md` is the design record for them.

**The data files carry no schema version** — there is no `schema_version`, `export_version`, or
similar marker in any data file — so the parser must tolerate missing optional fields and
unknown extras. The current format's manifest does carry a short `version` string (the first
versioned surface anywhere in the export), but the category files it points at remain
unversioned.

Every field below is marked by the container formats it was observed in: **(both)**,
**(legacy only)**, or **(current only)**. All examples are synthetic, using UUIDs of the form
`00000000-0000-0000-0000-0000000000xx` and placeholder content. No data from either reference
export is reproduced here.

---

## Container formats

### Legacy: single ZIP (May 2026 reference)

One ZIP archive holding everything:

```
users.json                              account record (list of 1)
memories.json                           memory blobs (list of 1)
conversations.json                      every chat (one large list)
projects/<project_uuid>.json            one file per Claude Project
```

The legacy reference export was 11 MB compressed: 134 conversations, 1,817 messages, 7
projects, 1 user, 1 global memory blob, 3 per-project memory blobs.

Anthropic delivered the archive with a structured filename:

```
data-<job_uuid>-<unix_timestamp>-<short_hash>-batch-<NNNN>.zip
```

The `batch-<NNNN>` suffix indicates the export pipeline was designed to support multi-batch
outputs. In observed cases (small accounts), only `batch-0000` was produced, but a parser
should accept multiple ZIPs and merge them, or warn if it sees a non-zero batch index in
isolation.

### Current: manifest + category ZIPs (August 2026 reference)

The emailed download link now delivers a **manifest** — a small standalone JSON file — that
lists four separately downloadable category ZIPs. The manifest filename embeds a job UUID, a
Unix timestamp, a short hash, and a human-readable timestamp:

```
manifest-<job_uuid>-<unix_timestamp>-<short_hash>-<YYYY-MM-DD-hh-mm-ss>.json
```

Manifest shape (synthetic):

```jsonc
{
  "version":     "1.0",                              // short version string; the only
                                                     // versioned surface in the export
  "created_at":  "2026-08-25T05:00:00.000000+00:00",
  "total_files": 4,
  "instructions": "Short prose telling the user to download each file below.",
  "data_files": [
    {
      "batch_index": 0,                              // ordinal over this list: 0,1,2,3
      "category":    "light_metadata",               // light_metadata | projects |
                                                     //   memories | conversations |
                                                     //   frames | artifact_data |
                                                     //   claude_code_cloud
      "part":        0,                              // part number within the category
      "filename":    "light_metadata-000.zip",
      "export_url":  "https://example.invalid/download/light_metadata-000.zip"
    }
    // … one entry per category zip
  ]
}
```

Observed values: `category` is one of `light_metadata`, `projects`, `memories`,
`conversations`, and — in the October 2026 sets — `frames`, `artifact_data`,
`claude_code_cloud`. Which categories appear varies by account and date (one October set
listed five files including `frames`; a later one listed all seven). `total_files` equals the
list length. `part` is `0` for every entry; `batch_index` enumerates the file list
(`0`–`3`), so unlike the legacy `batch-0000` suffix it is not constant. The `-000` in each
filename is the zero-padded part number, implying multi-part categories
(`conversations-001.zip`, …) are possible for larger accounts; no multi-part export has been
observed, so handling for it is structural tolerance, not verified behaviour. `export_url` is
a live download link — treat a saved manifest as sensitive.

Category ZIP contents:

| Category ZIP              | Members                                   | Legacy equivalent            |
|---------------------------|-------------------------------------------|------------------------------|
| `light_metadata-000.zip`  | `users.json`, `login_history.json` (root) | `users.json` (no login history) |
| `projects-000.zip`        | `projects/<project_uuid>.json`            | same, same subdirectory      |
| `memories-000.zip`        | `memories/<account-scoped uuid>.json`     | `memories.json` (root)       |
| `conversations-000.zip`   | `conversations.json` (root)               | same, root                   |
| `frames-000.zip`          | `artifacts/<artifact_uuid>/…` (see `frames`) | none                      |
| `artifact_data-000.zip`   | `artifacts/<artifact_uuid>/data/<name>.json` | none                      |
| `claude_code_cloud-000.zip` | `claude_code_cloud/routines.json`, `claude_code_cloud/routine_runs.json` | none |

Note the internal-layout inconsistency, preserved here as a format fact: **projects and
memories use a subdirectory inside their zips; conversations and light_metadata are
root-level.** A consumer cannot assume one rule for all categories.

The current reference export: `conversations-000.zip` was 38 MB compressed / 111 MB
uncompressed (259 conversations, 2,970 messages); `projects-000.zip` held 7 projects with 21
knowledge docs; `memories-000.zip` held one memory file; `light_metadata-000.zip` held one
user record and 12 login events.

**Timestamp forms by file** (inferred from uniform observed lengths; the profiler prints no
values): `conversations.json` timestamps — conversation, message, and content-block level —
are 27 characters in the current format, consistent with the `Z`-suffixed microsecond form
(`2026-08-25T05:00:00.000000Z`); every other file (manifest, projects, memories, login
history) uses 32-character values consistent with the explicit-offset form
(`2026-08-25T05:00:00.000000+00:00`). In the legacy format, conversation-level timestamps
used the `Z` form while message-level ones used the offset form. Both forms parse with
`datetime.fromisoformat` on Python 3.11+; a parser should accept either at any depth.

The October categories add more forms: `artifact.json` timestamps are 25 characters
(seconds with an explicit offset, `2026-10-01T12:00:00+00:00`); page comment timestamps 24
(milliseconds, `Z`); artifact data 32 (microseconds, offset); routine records 27
(microseconds, `Z`); run `scheduled_for` and one-off `run_once_at` 20 (seconds, `Z`); run
`started_at` 27–30 (six to nine fractional digits, `Z`). All parse with
`datetime.fromisoformat`, which keeps the first six fractional digits.

---

## `users.json` — (both)

A list of length 1, identical in shape across formats. In the current format it sits at the
root of `light_metadata-000.zip`.

```jsonc
[
  {
    "uuid":                  "00000000-0000-0000-0000-000000000001",
    "full_name":             "Synthetic User",
    "email_address":         "synthetic@example.invalid",
    "verified_phone_number": "+15555550100"            // E.164; may be empty
  }
]
```

Parser stance: treat the list-of-1 invariant as a hard requirement. Surface a clear error if a
future export contains zero or multiple records.

---

## `login_history.json` — (both)

At the root of `light_metadata-000.zip` in the current format. Later legacy single-ZIP
archives (observed in an August 2026 legacy archive) carry the same file, same shape, at the
archive root beside `users.json`; earlier legacy archives have none. **A sensitive surface**:
IP addresses, login timestamps, and device fingerprints for every recorded login.

```jsonc
{
  "login_events": [
    {
      "account_uuid": "00000000-0000-0000-0000-000000000001",
      "ip_address":   "203.0.113.10",
      "timestamp":    "2026-08-01T10:00:00.000000+00:00",
      "method":       "magic_link",                    // login method enum; only
                                                      //   magic_link observed
      "location_info": {
        "city":    null,                               // null throughout the reference
        "region":  null,                               //   archive
        "country": "US"                                // two-letter country code
      },
      "user_agent": {
        "browser_family": "Firefox",
        "browser_version": "142.0",
        "device_family":  "Other",
        "os_family":      "Windows",
        "os_version":     "11"
      }
    }
    // … 12 events in the reference export
  ]
}
```

All per-event fields were 100% present. `city` and `region` were null on every event in the
reference archive — treat them as nullable, not absent.

---

## Memories

The memory data changed shape with the container format — and with the product's memory
system. What the legacy format exports is now called the **Legacy Memory System** (a global
markdown blob plus per-project blobs); the current format adds the new memory system's
file-based structure alongside the legacy fields.

### Legacy: `memories.json` — (legacy only)

A list of length 1 at the archive root.

```jsonc
[
  {
    "conversations_memory": "**Work context**\n\nSynthetic markdown body...",
    "project_memories": {
      "00000000-0000-0000-0000-0000000000a1": "**Purpose & context**\n\n..."
      // 0..N entries — projects without memory simply omit the key
    },
    "account_uuid": "00000000-0000-0000-0000-000000000001"
  }
]
```

### Current: `memories/<uuid>.json` — (current only)

A single JSON file inside `memories-000.zip`, in a `memories/` subdirectory, named by a UUID
(account-scoped; the file's `account_uuid` field identifies the account). The top level is a
**dict, not a list-of-1** — a parser cannot reuse the legacy list handling unchanged.

```jsonc
{
  "account_uuid":         "00000000-0000-0000-0000-000000000001",
  "conversations_memory": "Opaque text — ~10 KB in the reference export.",   // (both)
  "project_memories": {                                                      // (both)
    "00000000-0000-0000-0000-0000000000a1": "Per-project memory text..."
    // 3 entries in the reference export, keyed by project UUID
  },
  "memory_files": [                                                          // (current only)
    {
      "path":       "/topics/synthetic-topic.md",
      "content":    "---\nname: synthetic-topic\n---\n\nBody text...",
      "updated_at": "2026-08-01T10:00:00.000000+00:00"
    }
    // … 18 files in the reference export
  ]
}
```

Field notes:

- `conversations_memory` — **(both)** — a single opaque text blob (markdown-ish; ~10 KB in
  the current reference). Treat as unstructured text. It is **frozen** once an account uses
  memory files: in two October 2026 exports weeks apart it was byte-identical while
  `memory_files` changed (and it differed from the August legacy archive's). The viewer
  treats it as legacy whenever memory files are present
  (`docs/export-categories-design.md` §7).
- `project_memories` — **(both)** — a dict keyed by project UUID, values are text blobs. A
  project with no memory is absent from the dict. A key may reference a project that is not
  in the projects category (**dangling project memory** — the viewer already preserves and
  flags this case); the current format's separately-downloadable categories make a
  memories/projects mismatch easier to produce, e.g. when only a subset of the zips is
  supplied.
- `memory_files` — **(current only)** — the new memory system. A list of
  `{path, content, updated_at}` records:
  - `path` is absolute-style with a leading `/`. Observed top-level entries in the
    reference export: `/profile.md`, `/preferences.md`, 8 files under `/topics/`, 8 under
    `/areas/`. This is an **open set**: the taxonomy is known to include other top-level
    directories (`/people/`, among others), and a parser must tolerate any layout —
    unknown directories, deeper nesting, files without extensions.
  - Every `content` in the reference export begins with a YAML frontmatter fence
    (`---`). Observed, not guaranteed — render tolerantly.
  - `updated_at` is per-file.

---

## `projects/<project_uuid>.json` — (both)

One JSON file per project, in a `projects/` subdirectory in both formats (inside
`projects-000.zip` in the current format). Field-identical across formats — the profiler
found the same ten keys, all 100% present, in both references. Project membership for
conversations is **not** recorded anywhere in either format — see "The project-membership
gap" below.

```jsonc
{
  "uuid":                "00000000-0000-0000-0000-0000000000a1",
  "name":                "Synthetic Project A",
  "description":         "Short prose describing the project.",
  "is_private":          false,
  "is_starter_project":  false,
  "prompt_template":     "Long instruction text given to Claude in this project.",
  "created_at":          "2026-02-25T10:00:00.000000+00:00",
  "updated_at":          "2026-04-30T17:00:00.000000+00:00",
  "creator": {
    "uuid":      "00000000-0000-0000-0000-000000000001",
    "full_name": "Synthetic User"
  },
  "docs": [
    {
      "uuid":       "00000000-0000-0000-0000-0000000000a2",
      "filename":   "synthetic-doc-1.md",
      "content":    "Extracted text body. Even for binary uploads this field holds text.",
      "created_at": "2026-02-25T10:05:00.000000+00:00"
    }
    // …
  ]
}
```

`prompt_template` may be the empty string (typical for starter projects) or several thousand
characters of system-prompt text. `docs` is a list of project knowledge documents — the body
is already extracted to text, so the parser does not need to read external files.

---

## `conversations.json` — (both)

A single top-level list, at the root of the archive (legacy) or of `conversations-000.zip`
(current). Each conversation has exactly seven fields, all 100% present in both references:

```jsonc
[
  {
    "uuid":          "00000000-0000-0000-0000-0000000000c1",
    "name":          "Synthetic conversation title",   // may be empty
    "summary":       "Auto-generated summary.",        // may be empty; up to ~5 KB in the
                                                       //   current reference
    "created_at":    "2026-08-05T10:11:30.196242Z",
    "updated_at":    "2026-08-05T10:25:32.013164Z",
    "account":       { "uuid": "00000000-0000-0000-0000-000000000001" },
    "chat_messages": [ /* … see next section … */ ]
  }
]
```

Things conspicuously absent at any depth in this file, in both formats: `project_uuid`,
`model`, `system`-sender messages. The conversation record carries no link back to a Claude
Project.

**Ordering**: conversations in the source list are not sorted by `created_at`, `updated_at`,
or `uuid` in either direction — the order is effectively random. The viewer must explicitly
choose a sort key when listing conversations.

**UUID schemes**: project UUIDs are time-ordered (UUID v7, leading prefix `019…`).
Conversation UUIDs are random (UUID v4). Sorting projects by uuid yields creation order;
sorting conversations by uuid does not.

---

## Message records (`chat_messages[*]`) — (both)

Every message has these nine fields, all 100% present in both references:

```jsonc
{
  "uuid":                "00000000-0000-0000-0000-0000000000c2",
  "text":                "Flat concatenation — see warning below.",
  "content":             [ /* … typed content blocks … */ ],
  "sender":              "human",                              // or "assistant"
  "created_at":          "2026-08-05T10:11:30.196242Z",        // current: Z form;
  "updated_at":          "2026-08-05T10:11:30.196242Z",        //   legacy: +00:00 offset form
  "attachments":         [],
  "files":               [],
  "parent_message_uuid": "00000000-0000-4000-8000-000000000000"
}
```

**Critical: `text` is unreliable.** The flat `text` field equals the joined plain-text content
in only ~55% of messages in the legacy reference. Whenever a message contains thinking blocks,
tool calls, or anything beyond plain text, `text` is incomplete. **The renderer must use the
`content` block list, not `text`.** `text` may still be useful as a fallback for
full-text-search indexing, but never as the canonical render source.

**Sender values**: only `"human"` and `"assistant"` observed in both references. There is no
`"system"` sender — project prompt templates do not appear as injected system messages, and
the current format's `injected_prompt_block` (below) arrives inside assistant/human messages'
content, not as a distinct sender. The parser tolerates unknown sender values (logs a
warning, retains the literal string) so a future addition does not crash it.

**Parent linkage**: the root message's `parent_message_uuid` is the sentinel
`00000000-0000-4000-8000-000000000000` (rarely `null`). All other parents reference another
message in the same conversation. Both references contained conversations where a parent has
multiple children — i.e. the user edited or regenerated, producing divergent branches. A
naive linear render of `chat_messages` would interleave alternate branches and look
incoherent. The renderer must walk the parent tree and choose a canonical path (default: the
chain ending at the message with the latest `created_at`).

---

## Content blocks (`chat_messages[*].content[*]`)

The legacy reference contained four block types; the current reference adds a fifth
(`injected_prompt_block`) and many block-level fields. Census across the current reference's
14,404 blocks: `text` 4,354; `thinking` 3,585; `tool_use` 3,229; `tool_result` 3,227;
`injected_prompt_block` 9. The October 2026 sets add a sixth, `document` (below).

All types share `type`, `flags` (null throughout both references), and
`start_timestamp`/`stop_timestamp` (nullable). Field lists below are per-type; fields new in
the current format are marked. The parser's preserve-and-flag stance applies to any type or
field not listed.

### `text` — (both)

```jsonc
{
  "type":            "text",
  "text":            "Body of the message.",
  "citations":       [ /* … see below; empty in the legacy reference … */ ],
  "citations_grouping_mode": "combine",     // (current only) rare — 4 of 4,354 blocks
  "flags":           null,
  "start_timestamp": "2026-08-05T10:11:30.196242Z",
  "stop_timestamp":  "2026-08-05T10:11:30.196242Z"
}
```

`citations` — empty lists throughout the legacy reference, populated in the current one (597
entries, every one a web-search citation):

```jsonc
{
  "uuid":        "00000000-0000-0000-0000-0000000000e1",
  "start_index": 100,                       // offsets into the block's text
  "end_index":   140,
  "details": {
    "type": "web_search_citation",          // only observed value
    "url":  "https://example.invalid/cited-page"
  }
}
```

### `thinking` — (both, heavily extended in current)

```jsonc
{
  "type":                     "thinking",
  "thinking":                 "Internal reasoning text.",
  "summaries":                [                                  // populated in current:
    { "summary": "Synthetic summary of a reasoning step." }      //   3,026 of 3,585 blocks
  ],                                                             //   non-empty
  "signature":                "opaque-base64-like-string",       // nullable; large — up to
                                                                 //   ~149 KB observed
  "truncated":                false,
  "cut_off":                  false,
  "hidden":                   false,        // (current only)
  "thinking_hidden":          false,        // (current only) true on 174 of 3,585
  "alternative_display_type": null,
  "flags":                    null,
  "start_timestamp":          "...",
  "stop_timestamp":           "..."
}
```

In the legacy reference, `summaries` was always empty and `signature` always null. In the
current reference both are populated; `signature` is an opaque blob with no render value —
a consumer should preserve it but never display it inline.

### `tool_use` — (both, extended in current)

A call from Claude to a built-in tool, an MCP tool, or the artifacts system.

```jsonc
{
  "type":                 "tool_use",
  "id":                   "toolu_synthetic_1",  // nullable
  "name":                 "web_search",         // or "artifacts", or "<server>:<tool>"
  "input":                { "query": "…" },     // tool-specific payload
  "message":              "Searching the web…",
  "icon_name":            "search",
  "integration_name":     null,                 // e.g. an MCP server's display name
  "integration_icon_url": null,
  "context":              null,
  "display_content":      null,                 // populated in current — see below
  "approval_options":     null,
  "approval_key":         null,
  "approval_key_legacy":  null,                 // (current only)
  "is_mcp_app":           null,                 // boolean when the tool is an MCP app
  "mcp_server_url":       null,
  "hidden_in_chat":       null,                 // (current only)
  "tool_identifier":      null,                 // (current only)
  "tool_origin":          "first_party",        // (current only) sparse — 204 of 3,229
  "flags":                null,
  "start_timestamp":      "...",
  "stop_timestamp":       "..."
}
```

**Artifacts are encoded as `tool_use` with `name == "artifacts"`.** The `input` field on an
artifact call has the shape:

```jsonc
{
  "command":      "create",       // create | update | rewrite
  "id":           "synthetic-artifact-1",
  "type":         "text/markdown",
  "language":     null,
  "title":        "Synthetic artifact",
  "content":      "# Body…",
  "source":       null,
  "md_citations": [],
  "version_uuid": "00000000-0000-0000-0000-0000000000e2"
}
```

This is what a "download artifacts" feature must extract.

> **Superseded for artifact blocks.** The example above predates the v1.1 Session D discovery
> pass and carries pre-discovery guesses. The authoritative description of artifact blocks —
> observed commands, null block-level ids and positional pairing, `md_citations` carrying
> real offsets, the truncation and reconstruction verdicts — is `docs/artifact-encoding.md`.

**MCP namespacing**: tool names of the form `<server>:<tool>` indicate MCP tools. The colon
is the separator. The parser stores the full name verbatim; splitting is a render-time
concern.

### `tool_result` — (both, extended in current)

The reply to a `tool_use`.

```jsonc
{
  "type":                 "tool_result",
  "tool_use_id":          "toolu_synthetic_1",  // nullable
  "name":                 "web_search",
  "is_error":             false,
  "content":              [ /* … nested inner blocks … */ ],
  "structured_content":   null,                 // dict on some blocks in current — see below
  "meta":                 null,                 // or { "output_format_category": "md" }
  "message":              null,
  "display_content":      null,                 // populated in current — see below
  "integration_name":     null,
  "integration_icon_url": null,
  "icon_name":            "search",
  "mcp_server_url":       null,
  "hidden_in_chat":       null,                 // (current only)
  "flags":                null,
  "start_timestamp":      null,
  "stop_timestamp":       null
}
```

`meta`, when present (a fifth of tool_results in the current reference), holds a single
`output_format_category` enum; observed values `md`, `none`, `other`, `svg`, `react`, `html`.

`structured_content`, when a dict (15 blocks in the current reference), describes a
file-tool target: `{parsed, path, path_segments, target, updated_at, version}` — or a
`documents` list of the same shape. Sparse and evolving; preserve-and-flag. In the October
2026 sets its key set widened (memory-tool, spreadsheet, and revision keys among them), and
the result of an `Artifact` publishing call carries the published artifact's id and link
*(current only, October 2026)*:

```jsonc
"structured_content": {
  "artifact_id": "00000000-0000-0000-0000-0000000a0002",   // a frames artifact id
  "url":         "https://example.invalid/artifact/00000000-0000-0000-0000-0000000a0002"
}
```

`meta` may also carry `tool_references` *(current only, October 2026)*: a list of short
strings naming tools (one to three entries observed).

Tool `input` is an open set and gains keys freely. One October shape matters to the viewer:
Docs-connector calls carry `input.container.id`, which can name a `frames` artifact of kind
`page`. Artifact ids also appear in tool-result prose; the viewer's cross-links use only
`input` and `structured_content` (`docs/export-categories-design.md` §6.5).

The nested `content` list inside a `tool_result` carries sub-blocks of these types:

| Inner `type`      | Distinguishing fields                                                              | Observed in |
|-------------------|------------------------------------------------------------------------------------|-------------|
| `knowledge`       | `is_citable`, `is_missing`, `links` *(current only; list or null)*, `metadata`, `prompt_context_metadata`, `text`, `title`, `url` | both |
| `text`            | `text`, `uuid` *(uuid often absent in current)*                                    | both        |
| `local_resource`  | `file_path`, `mime_type`, `name`, `uuid` — analysis-tool file outputs              | both        |
| `image`           | `file_uuid` — binary not bundled in the archive                                    | legacy only |

`image` not appearing in the current reference is an absence in that archive, not evidence of
removal — keep handling it.

Knowledge metadata fields (`metadata.site_domain`, `metadata.favicon_url`,
`prompt_context_metadata.search_provider`, `prompt_context_metadata.url`, plus current-format
additions such as `prompt_context_metadata.age`, `.content_type`, `.destination_url`,
`.final_url`, `.mime_type`) describe the web source of a search hit. They do not carry
project-membership information.

### `display_content` on tool blocks — (current only, in practice)

The `display_content` field exists on tool blocks in both formats but was always null in the
legacy reference. In the current reference it is a dict on 2,311 tool blocks: a rendering
hint for how the product displayed the call or result. Its `type` selects which sibling field
carries the payload; observed values with their payload fields:

| `display_content.type` | Payload fields                                                        |
|------------------------|-----------------------------------------------------------------------|
| `json_block`           | `json_block` (a JSON document as a string, up to 64 KB observed)      |
| `text`                 | `text` (short display string)                                         |
| `rich_link`            | `link` `{icon_url, resource_type, source, subtitles, title, url}`, `is_trusted` |
| `table`                | `table` (list of rows, each a list of cell strings)                   |
| `rich_content`         | `content` (list of link-shaped dicts as in `rich_link`)               |
| `code_block`           | `code`, `filename`, `language`                                        |

Treat the set as open: preserve-and-flag any unknown `display_content.type`.

### `injected_prompt_block` — (current only)

A new block type (9 instances in the current reference): prompt text the product injected
into the conversation (e.g. a skill or feature preamble).

```jsonc
{
  "type":                           "injected_prompt_block",
  "prompt":                         "Injected instruction text, ~1–2 KB observed.",
  "initial_turn_only":              false,
  "skip_on_truncated_continuation": false,
  "flags":                          null,
  "start_timestamp":                "...",
  "stop_timestamp":                 "..."
}
```

### `document` — (current only, October 2026)

A document the user attached to their turn — always on `human` messages in the observed set
(22 blocks). The file itself is not in the export; the block names it.

```jsonc
{
  "type":            "document",
  "file_uuid":       "00000000-0000-0000-0000-0000000000d0",
  "title":           "Synthetic brief.pdf",     // occasionally absent (1 of 22)
  "flags":           null,
  "start_timestamp": "...",
  "stop_timestamp":  "..."
}
```

`title` and `file_uuid` are block-level keys only on this type in the observed set.

---

## `attachments` and `files` on messages — (both)

Two distinct mechanisms, unchanged across formats. Most messages have neither.

### `attachments` — inline text

```jsonc
{
  "file_name":         "notes.txt",       // may be empty
  "file_type":         "txt",
  "file_size":         14967,
  "extracted_content": "Body text…"
}
```

The body is embedded. The renderer can display `extracted_content` directly. Observed
`file_type` values include short extensions (`txt`), MIME types (`text/markdown`,
`text/plain`, `text/javascript`, `application/json`, and — October 2026 — `text/vtt`), and the
empty string; treat it as an open set.

### `files` — binary references

```jsonc
{
  "file_uuid": "00000000-0000-0000-0000-0000000000d8",
  "file_name": "screenshot.png"
}
```

Only the filename and a UUID are present. **The binary itself is not in the export**, in
either format. The renderer can show the filename and a "binary not included in export"
notice.

---

## `frames` — published artifacts — (current only, October 2026)

One directory per published artifact under `artifacts/<artifact_uuid>/` (three artifacts in
the larger observed set):

```
artifacts/<artifact_uuid>/artifact.json                       descriptor (always)
artifacts/<artifact_uuid>/versions/<version_id>.html          single-HTML version
artifacts/<artifact_uuid>/versions/<version_id>.files.json    multi-file version: file list…
artifacts/<artifact_uuid>/versions/<version_id>/…             …beside a directory of files
artifacts/<artifact_uuid>/<name>.md                           page kind: the content
artifacts/<artifact_uuid>/<name>.json                         page kind: comment threads
```

A version is laid out one way or the other, never both; a version may also have no files at
all (every `page`-kind version observed). File names below the artifact directory are
user-authored and vary — consumers find them by layout and extension, never by name.

### `artifact.json`

```jsonc
{
  "id":             "00000000-0000-0000-0000-0000000a0002",   // equals the directory name
  "kind":           "artifact",          // artifact | page — open set
  "visibility":     "private",           // only private observed — open set
  "owner_account":  "00000000-0000-0000-0000-000000000001",   // the account uuid
  "updated_at":     "2026-10-01T12:00:00+00:00",
  "active_version": "1790000000-e5f6",   // always present in the list (observed)
  "versions": [
    {
      "id":          "1790000000-e5f6",  // <unix-seconds>-<4 hex>; seconds == created_at
      "title":       "Synthetic reading log",
      "description": "Free text describing the version.",
      "created_at":  "2026-10-01T12:00:00+00:00"
    }
    // … one to seven versions observed per artifact
  ]
}
```

The `versions` list carries no ordering guarantee a consumer should rely on (newest-first in
the one multi-version artifact observed); sort by `created_at`. `active_version` was the
newest version in every observed artifact, but nothing promises that either.

### `<version_id>.files.json` — multi-file versions

```jsonc
{
  "files": [
    {
      "original_path": "index.html",       // relative, depth 0–2, no leading "/"
      "exported_as":   "index.html",       // equal to original_path in every observed entry
      "sha256":        "<64 hex>",
      "size_bytes":    360,
      "mime_type":     "text/html"         // text/html | text/markdown | text/css |
                                           //   text/javascript | font/woff2 | application/json
    }
    // … seven to eleven entries per version observed
  ]
}
```

Every listed file was present under `versions/<version_id>/` at its `exported_as` path with a
matching size and SHA-256; exactly one entry per version is `text/html` at depth 0 (the entry
page). The entry page is a small shell beside large bundled scripts and stylesheets (up to
1.5 MB of JavaScript observed); the authored content lives in the `text/markdown` files. A
multi-file version may include a small capability configuration as its only JSON file:

```jsonc
{ "v": 1, "source": "db", "db": { "meta": { … }, "collections": [ … ] } }
```

Single-HTML versions are complete pages with a doctype; the observed one linked an external
web font and carried no scripts. Every observed multi-file entry page carried a script.

### `page`-kind content and comments

A `page` artifact has no version files. Its content is a root-level Markdown file and its
comments a root-level JSON list of threads:

```jsonc
[
  {
    "resolved":    false,
    "tab":         "…",                  // an opaque tab reference
    "quoted_text": "First point",        // the passage the thread is anchored to
    "comments": [
      { "body": "Synthetic open question.", "created_at": "2026-10-01T12:00:00.000Z" }
    ]
  }
]
```

---

## `artifact_data` — data saved by artifacts — (current only, October 2026)

```
artifacts/<artifact_uuid>/data/<name>.json
```

The artifact uuid matched a `frames` artifact in the observed set. Each file:

```jsonc
{
  "documents": [
    {
      "collection": "log_entries",       // user-defined collection name
      "id":         "entry-0001",        // user-defined document id
      "created_at": "2026-10-01T12:00:00.000000+00:00",
      "updated_at": "2026-10-01T12:00:00.000000+00:00",
      "data":       { "…": "…" }         // open set of user-defined keys (1–6 observed)
    }
  ]
}
```

`data` is never interpreted: its keys and values are whatever the artifact stored.

---

## `claude_code_cloud` — scheduled tasks — (current only, October 2026)

Claude Code scheduled tasks ("routines") and their runs. Both files wrap their records in a
`claude_code_cloud` list.

`claude_code_cloud/routines.json` (three routines observed):

```jsonc
{
  "claude_code_cloud": [
    {
      "routine_id":      "trig_01SYNTHETICCRON00000001",   // trig_ prefix
      "agent_id":        null,                              // null throughout
      "name":            "Synthetic weekly tidy-up",
      "prompt":          "The task's prompt text (1–3 KB observed).",
      "prompt_withheld": false,                             // true: prompt not exported
      "enabled":         true,
      "schedule": {
        "cron":        "0 9 * * 1",                         // exactly one of these two
        "run_once_at": null                                 //   is non-null
      },
      "created_at":      "2026-10-01T09:00:00.250000Z",
      "updated_at":      "2026-10-02T09:00:00.250000Z",
      "ended_at":        null
    }
  ]
}
```

`claude_code_cloud/routine_runs.json` (13 runs observed, all for one routine — most routines
had none):

```jsonc
{
  "claude_code_cloud": [
    {
      "routine_id":    "trig_01SYNTHETICCRON00000001",   // resolved to a routine every time
      "scheduled_for": "2026-10-08T09:00:00Z",
      "started_at":    "2026-10-08T09:00:04.123456789Z",
      "finished_at":   "2026-10-08T09:03:41.654321Z",
      "status":        "succeeded",                       // only value observed
      "session_id":    "cse_01SYNTHETICSESSION0001"       // cse_ prefix; no session content
    }
  ]
}
```

---

## The project-membership gap — (both)

This was the most surprising finding from the legacy inventory pass, and **the current format
re-confirms it**: the August 2026 reference's conversation records carry exactly the same
seven fields as the legacy format — no project field appeared anywhere in the profile.
**Conversations carry no link to the Claude Project they belong to**, and project files carry
no list of conversations. Every candidate signal was checked against the legacy reference:

- No `project_uuid` or `*project*` field exists at any depth in `conversations.json`.
- No `conversation_uuid` or chat reference exists in any `projects/<uuid>.json`.
- The literal project UUID strings never appear anywhere in `conversations.json`.
- `project_knowledge_search` tool calls record only `{query, max_text_results}` — no project id.
- Knowledge-block metadata is web-search metadata only.
- Project `prompt_template` text is not injected as a system message (no `"system"` sender).
- Conversation ordering in the JSON is effectively random, not project-clustered.
- Project names do not appear in conversation text bodies.

Conclusion: in both observed formats, project→conversation membership must be supplied
out-of-band by the viewer (e.g. a manual user-tagging UI persisted in a local SQLite table),
because it is not recoverable from the archive.
