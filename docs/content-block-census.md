# Content-block census (v1.2 Session L)

**Status**: discovery deliverable, Session L (2026-07). This census answers one question posed
by `docs/export-design.md`: does the design record's rendering table cover every content-block
type and sender value that actually occurs in the real export? **Answer: yes — no unmapped
types, no unmapped senders. No rendering decisions are required before Session M.** The
supporting aggregates follow.

## Method

- Tool: `tools/scan_content_blocks.py`, a generalisation of the `tools/scan_artifacts.py`
  pattern — stdlib-only, aggregate-only output. It walks every message of every conversation
  in `conversations.json` (all branches, not just the canonical path) and reduces to counts,
  type names, tool names, and coarse size buckets. No conversation content, titles, or
  identifiers appear in its output or in this document.
- Run against two generations of the same account's export:
  - **Current export** (the archive loaded in the viewer): 213 conversations. This is the
    primary census target, and the same generation Session D scanned — its artifact numbers
    reproduce that baseline exactly (see below).
  - **May 2026 export** (the older archive described in `docs/export-schema.md`):
    134 conversations. Re-scanned as a cheap cross-generation stability check.
- "Conversations touched" counts distinct conversations containing at least one block of the
  type, across all branches.
- Attachment sizes are UTF-8 byte lengths of `extracted_content`, bucketed coarsely; the
  buckets size the attachment-toggle decision, not a storage budget.
- Session D privacy rules applied throughout: scripted aggregates only; the session-end
  staged-diff content grep was run on this document before commit.

## Export shape

| Measure | Current export | May 2026 export |
|---|---:|---:|
| Conversations | 213 | 134 |
| Messages (all branches) | 2,743 | 1,817 |
| Content blocks | 11,617 | 6,259 |

## Content-block types

Both archives contain **exactly four** top-level content-block types — the same four the
rendering table covers. Nothing else occurs.

| Block type | Blocks (current) | Conversations touched (current) | Blocks (May 2026) |
|---|---:|---:|---:|
| `text` | 3,939 | 205 | 2,327 |
| `thinking` | 2,950 | 201 | 1,712 |
| `tool_use` | 2,365 | 185 | 1,110 |
| `tool_result` | 2,363 | 185 | 1,110 |

**Unmapped block types: none** (in either archive).

Observation for Session M (not a gap in the table): the current export has **two `tool_use`
blocks with no `tool_result` anywhere** (2,365 vs 2,363; the deficit sits on `web_search` and
`create_file`, one each — presumably interrupted calls). The serializer's tool-card rule should
tolerate an absent result rather than assume pairing, mirroring the reading view.

## Senders

| Sender | Messages (current) | Messages (May 2026) |
|---|---:|---:|
| `assistant` | 1,379 | 914 |
| `human` | 1,364 | 903 |

Only the two known values occur; the `## User` / `## Assistant` heading rule covers everything
observed, and the raw pass-through for unknown senders remains a dormant (but required)
preserve-and-flag path.

## Thinking blocks

| Measure | Current | May 2026 |
|---|---:|---:|
| Thinking blocks | 2,950 | 1,712 |
| `truncated` flagged | 0 | 0 |
| `cut_off` flagged | 0 | 0 |

The design record's flag-carrying label variant (`> **[Thinking]**` with a truncated/cut-off
marker) is exercised by **zero** real blocks — it must be covered by synthetic fixtures, not
real-data testing. Thinking touches 201 of 213 conversations, which confirms the weight of the
default-off toggle decision: nearly every exported file will carry the `thinking: omitted`
frontmatter marker under the default.

## Tool-use names

Names only, per the privacy rules. Current export; 22 distinct names:

| Tool name | Calls |
|---|---:|
| `web_search` | 1,127 |
| `bash_tool` | 264 |
| `view` | 230 |
| `web_fetch` | 148 |
| `create_file` | 143 |
| `present_files` | 134 |
| `str_replace` | 132 |
| `launch_extended_search_task` | 38 |
| `artifacts` | 34 |
| `ask_user_input_v0` | 29 |
| `conversation_search` | 26 |
| `project_knowledge_search` | 18 |
| `Claude in Chrome:computer` | 15 |
| `visualize:show_widget` | 8 |
| `visualize:read_me` | 5 |
| `recent_chats` | 4 |
| `Claude in Chrome:find` | 4 |
| `Claude in Chrome:tabs_context_mcp` | 2 |
| `Claude in Chrome:navigate` | 1 |
| `Claude in Chrome:left_click` | 1 |
| `places_search` | 1 |
| `places_map_display_v0` | 1 |

The May 2026 archive's 18 names are a subset (it predates the `visualize:*` and `places_*`
tools). MCP-namespaced names (`<server>:<tool>`) pass into the tool card verbatim, per the
design record — no new handling needed.

## Tool results

- `is_error` (current): 2,277 false, 86 true. The one-line card rule applies to both; whether
  the card shows an error marker is a reading-view-parity question for Session M, not a census
  gap.
- Inner block types inside `tool_result.content` (current): `knowledge` 9,705, `text` 1,122,
  `local_resource` 158, `image` 8 — the same four types `docs/export-schema.md` documents.
  All are subsumed by the one-line card rule; the serializer never descends into them.

## Attachments

| Measure | Current | May 2026 |
|---|---:|---:|
| Attachments | 23 | 5 |
| Messages with attachments | 17 | 5 |
| Extracted text <1 KB | 0 | 0 |
| Extracted text 1–10 KB | 13 | 2 |
| Extracted text 10–100 KB | 10 | 3 |
| Extracted text >100 KB | 0 | 0 |
| Empty or missing extracted text | 0 | 0 |

Toggle-impact reading: attachment text is real but bounded — no attachment exceeds 100 KB, and
roughly half sit in the 10–100 KB bucket. The default-on decision costs at most a few hundred
kilobytes across a full bulk export of this account; the off position exists for compactness,
not necessity. Every attachment has non-empty extracted text, so the "label line + continued
blockquote" rule always has a body to carry when the toggle is on.

## Files

Current export: 112 file references across 57 messages (May 2026: 28 across 18). All are
covered by the one-line reference notice — the binaries are not in the archive.

## Artifacts — format-shift check against the Session D baseline

| Measure | Session D baseline | This census (current export) |
|---|---:|---:|
| Artifact calls | 34 | 34 |
| `create` | 34 | 34 |
| `update` | 0 | 0 |
| `rewrite` | 0 | 0 |
| Conversations with artifact calls | 28 | 28 |

Byte-for-byte reproduction of the baseline: **no format shift**. The archive remains
create-only; edit-replay semantics stay unverified inference, and the placeholder-card rule
covers every observed call. (The May 2026 archive shows 28 create-only calls in 22
conversations — fewer because it is older, same shape.)

## Mapping: every observed type → its rendering rule

| Observed | Rendering rule in `docs/export-design.md` | Covered? |
|---|---|---|
| `text` block | Verbatim stored Markdown, unescaped, unwrapped | Yes |
| `thinking` block | `> **[Thinking]**` blockquote, export-time toggle, default off | Yes |
| `tool_use` block (21 non-artifact names) | `> **[Tool: <name>]** <result summary>` one-line card | Yes |
| `tool_use` block, `name == "artifacts"` | `> **[Artifact: <title> (<type>)]** — content not exported.` | Yes |
| `tool_result` block (all inner types) | Folded into the tool-use card's result summary | Yes |
| `sender: human` / `sender: assistant` | `## User` / `## Assistant` headings | Yes |
| Message `attachments` | Label line + extracted text, toggle default on | Yes |
| Message `files` | One-line reference notice | Yes |
| Unknown block types | `> **[Unrecognised content block: <type>]**` | Dormant — none observed |
| Unknown senders | Raw pass-through | Dormant — none observed |

**Decisions required before Session M: none.** The census found no unmapped types. Two minor
observations are recorded for the implementation brief rather than as gaps: the unpaired
`tool_use` tolerance and the error-marker parity question, both under "Tool results" above.
