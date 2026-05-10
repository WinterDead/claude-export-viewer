# Claude.ai data-export schema

Reference for the structure of the ZIP archive that Anthropic emails when a user requests their
Claude.ai data export. All field shapes here were determined empirically from a real export
(May 2026, single-account); they are not pulled from public documentation. **Anthropic does not
version the export format** — there is no `schema_version`, `export_version`, or similar marker
anywhere in the archive — so the parser must tolerate missing optional fields and unknown extras.

All examples below use synthetic UUIDs of the form `00000000-0000-0000-0000-0000000000xx` and
placeholder content. No data from the reference export is reproduced here.

---

## File inventory

A typical export contains:

```
users.json                              account record (list of 1)
memories.json                           memory blobs (list of 1)
conversations.json                      every chat (one large list)
projects/<project_uuid>.json            one file per Claude Project
```

The reference export was 11 MB compressed: 134 conversations, 1,817 messages, 7 projects, 1 user,
1 global memory blob, 3 per-project memory blobs.

### Filename and batching

Anthropic delivers the archive with a structured filename:

```
data-<job_uuid>-<unix_timestamp>-<short_hash>-batch-<NNNN>.zip
```

The `batch-<NNNN>` suffix indicates the export pipeline is designed to support multi-batch
outputs. In observed cases (small accounts), only `batch-0000` is produced, but the parser should
accept multiple ZIPs and merge them, or warn if it sees a non-zero batch index in isolation.

---

## `users.json`

A list of length 1. The single record describes the account that requested the export.

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

## `memories.json`

A list of length 1. The single record contains the global memory blob and a dictionary of
per-project memory blobs.

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

Both `conversations_memory` and each value of `project_memories` are **markdown strings**
(headings, bold, lists). In the reference export, only 3 of 7 projects had a memory entry. A
project with no memory does not appear as an empty string — its UUID is absent from the dict.

---

## `projects/<project_uuid>.json`

One JSON file per project, in a `projects/` directory inside the archive. Project membership for
conversations is **not** recorded anywhere — see "The project-membership gap" below.

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
characters of system-prompt text. `docs` is a list of project knowledge documents — the body is
already extracted to text, so the parser does not need to read external files.

---

## `conversations.json`

A single top-level list. Each conversation has exactly seven fields, all 100% present in the
reference export:

```jsonc
[
  {
    "uuid":          "00000000-0000-0000-0000-0000000000c1",
    "name":          "Synthetic conversation title",   // may be empty (≈5% of conversations)
    "summary":       "Short auto-generated summary.",  // may be empty (≈25% of conversations)
    "created_at":    "2026-04-05T10:11:30.196242Z",    // note: Z suffix here…
    "updated_at":    "2026-04-05T10:25:32.013164Z",
    "account":       { "uuid": "00000000-0000-0000-0000-000000000001" },
    "chat_messages": [ /* … see next section … */ ]
  }
]
```

Things conspicuously absent at any depth in this file: `project_uuid`, `model`, `system`-sender
messages. The conversation record carries no link back to a Claude Project.

**Ordering**: conversations in the source list are not sorted by `created_at`, `updated_at`, or
`uuid` in either direction — the order is effectively random (likely insertion order in some
internal table). The viewer must explicitly choose a sort key when listing conversations.

**UUID schemes**: project UUIDs are time-ordered (UUID v7, leading prefix `019…`). Conversation
UUIDs are random (UUID v4). Sorting projects by uuid yields creation order; sorting conversations
by uuid does not.

---

## Message records (`chat_messages[*]`)

Every message has these nine fields, all 100% present:

```jsonc
{
  "uuid":                "00000000-0000-0000-0000-0000000000c2",
  "text":                "Flat concatenation — see warning below.",
  "content":             [ /* … typed content blocks … */ ],
  "sender":              "human",                              // or "assistant"
  "created_at":          "2026-04-05T10:11:30.196242+00:00",   // note: offset, not Z
  "updated_at":          "2026-04-05T10:11:30.196242+00:00",
  "attachments":         [],
  "files":               [],
  "parent_message_uuid": "00000000-0000-4000-8000-000000000000"
}
```

**Critical: `text` is unreliable.** The flat `text` field equals the joined plain-text content
in only ~55% of messages in the reference export. Whenever a message contains thinking blocks,
tool calls, or anything beyond plain text, `text` is incomplete. **The renderer must use the
`content` block list, not `text`.** `text` may still be useful as a fallback for full-text-search
indexing, but never as the canonical render source.

**Sender values**: only `"human"` and `"assistant"` were observed. There is no `"system"`
sender — project prompt templates do not appear as injected system messages in the export. The
parser tolerates unknown sender values (logs a warning, retains the literal string) so a future
addition does not crash it.

**Timestamp formats**: conversation timestamps use a `Z` suffix (`...Z`), message timestamps use
an explicit offset (`...+00:00`). Both forms are accepted by `datetime.fromisoformat` on Python
3.11+.

**Parent linkage**: the root message's `parent_message_uuid` is the sentinel
`00000000-0000-4000-8000-000000000000` (rarely `null`). All other parents reference another
message in the same conversation. In the reference export, 12 of 134 conversations had at least
one parent with multiple children — i.e. the user edited or regenerated, producing divergent
branches. A naive linear render of `chat_messages` would interleave alternate branches and look
incoherent in those conversations. The renderer must walk the parent tree and choose a canonical
path (default: the chain ending at the message with the latest `created_at`).

---

## Content blocks (`chat_messages[*].content[*]`)

Four block types were observed across the reference export. All four share `start_timestamp`,
`stop_timestamp`, `flags`, and `type`.

### `text`

Plain message text. The most common block type.

```jsonc
{
  "type":            "text",
  "text":            "Body of the message.",
  "citations":       [],
  "flags":           null,
  "start_timestamp": "2026-04-05T10:11:30.196242+00:00",
  "stop_timestamp":  "2026-04-05T10:11:30.196242+00:00"
}
```

### `thinking`

Extended-thinking reasoning trace.

```jsonc
{
  "type":                     "thinking",
  "thinking":                 "Internal reasoning text.",
  "summaries":                [],
  "signature":                null,
  "truncated":                false,
  "cut_off":                  false,
  "alternative_display_type": null,
  "flags":                    null,
  "start_timestamp":          "...",
  "stop_timestamp":           "..."
}
```

### `tool_use`

A call from Claude to a built-in tool, an MCP tool, or the artifacts system.

```jsonc
{
  "type":                 "tool_use",
  "id":                   "toolu_synthetic_1",
  "name":                 "web_search",        // or "artifacts", or "<server>:<tool>"
  "input":                { "query": "…" },    // tool-specific payload
  "message":              "Searching the web…",
  "icon_name":            "search",
  "integration_name":     null,                // e.g. "Claude in Chrome" for MCP
  "integration_icon_url": null,
  "context":              null,
  "display_content":      null,
  "approval_options":     null,
  "approval_key":         null,
  "is_mcp_app":           null,
  "mcp_server_url":       null,
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
  "type":         "text/html",
  "language":     "html",
  "title":        "Synthetic artifact",
  "content":      "<!doctype html>…",
  "source":       null,
  "md_citations": [],
  "version_uuid": "00000000-0000-0000-0000-0000000000e2"
}
```

This is what a "download artifacts" feature must extract.

**MCP namespacing**: tool names of the form `<server>:<tool>` (e.g. `Claude in Chrome:computer`)
indicate MCP tools. The colon is the separator. The parser stores the full name verbatim;
splitting is a render-time concern.

### `tool_result`

The reply to a `tool_use`.

```jsonc
{
  "type":                 "tool_result",
  "tool_use_id":          "toolu_synthetic_1",
  "name":                 "web_search",
  "is_error":             false,
  "content":              [ /* … nested inner blocks … */ ],
  "structured_content":   null,
  "meta":                 null,
  "message":              null,
  "display_content":      null,
  "integration_name":     null,
  "integration_icon_url": null,
  "icon_name":            "search",
  "mcp_server_url":       null,
  "flags":                null,
  "start_timestamp":      null,
  "stop_timestamp":       null
}
```

The nested `content` list inside a `tool_result` carries one or more sub-blocks of these types:

| Inner `type`      | Distinguishing fields                                                              |
|-------------------|------------------------------------------------------------------------------------|
| `knowledge`       | `is_citable`, `is_missing`, `metadata`, `prompt_context_metadata`, `text`, `title`, `url` |
| `text`            | `text`, `uuid`                                                                     |
| `local_resource`  | `file_path`, `mime_type`, `name`, `uuid` — analysis-tool file outputs              |
| `image`           | `file_uuid` — binary not bundled in the archive                                    |

Knowledge metadata fields (`metadata.site_domain`, `metadata.favicon_url`,
`prompt_context_metadata.search_provider`, `prompt_context_metadata.url`, etc.) describe the web
source of a search hit. They do not carry project-membership information.

---

## `attachments` and `files` on messages

Two distinct mechanisms. Most messages have neither (population was ~0.3% and ~1% respectively
in the reference export).

### `attachments` — inline text

```jsonc
{
  "file_name":         "notes.txt",       // may be empty
  "file_type":         "txt",
  "file_size":         14967,
  "extracted_content": "Body text…"
}
```

The body is embedded. The renderer can display `extracted_content` directly.

### `files` — binary references

```jsonc
{
  "file_uuid": "00000000-0000-0000-0000-0000000000d8",
  "file_name": "screenshot.png"
}
```

Only the filename and a UUID are present. **The binary itself is not in the export ZIP.** The
renderer can show the filename and a "binary not included in export" notice.

---

## The project-membership gap

This is the most surprising finding from the inventory pass. **Conversations carry no link to
the Claude Project they belong to**, and project files carry no list of conversations. Every
candidate signal was checked:

- No `project_uuid` or `*project*` field exists at any depth in `conversations.json`.
- No `conversation_uuid` or chat reference exists in any `projects/<uuid>.json`.
- The literal project UUID strings never appear anywhere in `conversations.json`.
- `project_knowledge_search` tool calls record only `{query, max_text_results}` — no project id.
- Knowledge-block metadata is web-search metadata only.
- Project `prompt_template` text is not injected as a system message (no `"system"` sender).
- Conversation ordering in the JSON is effectively random, not project-clustered.
- Project names do not appear in conversation text bodies.

Conclusion: as of the May 2026 export format, project→conversation membership must be supplied
out-of-band by the viewer (e.g. a manual user-tagging UI persisted in a local SQLite table),
because it is not recoverable from the archive.
