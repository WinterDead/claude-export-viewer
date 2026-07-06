# Conversation Export — Design Record (v1.2)

**Status**: design record, decided 2026-07 (operator + chat). This document describes
*intended* behaviour: its claims are design intent, not verified documentation. The v1.2
implementation briefs and their verification matrices are what promote it, section by
section, to verified status. Commit this file as `docs/export-design.md` via the opening
v1.2 brief; edit it only through the same chat-authored channel as all design decisions.

## What the feature is

Export a conversation — or a selection of conversations — from the viewer to Markdown files
downloaded through the browser. The exported file is *the conversation as a document*:
faithful to the stored message text, honest about what it omits, and useful in any Markdown
tool.

## Structural decisions (locked)

- **The serializer assembles stored Markdown; it never converts rendered HTML back.**
  Message text in the export is already Markdown; export reassembles that source verbatim
  with structural wrappers around it. The markdown → bleach rendering pipeline is not
  involved at any point, so this feature adds no sanitiser surface.
- **All output is a browser download.** Single conversation → one `.md` file; bulk → one
  `.zip` of `.md` files. The app never writes to a user-chosen filesystem path; there is no
  output-location setting. (This deliberately shrinks the v1.2.5 delta-audit surface.)
- **Tool activity exports as one-line labelled cards**, mirroring the reading view. Full
  tool/file/artifact bodies are the 1.3+ extraction feature. The boundary rule for every
  future brief: if an export brief starts including tool or artifact *bodies*, it has
  crossed into extraction and must stop.

## File format

### Frontmatter + title

YAML frontmatter, then an H1 title. Fields:

```yaml
---
title: "<conversation title, or the synthesised preview for untitled conversations>"
summary: "<conversation summary>"     # only when the conversation has one
uuid: <conversation uuid>
created: <ISO 8601, from the export>
updated: <ISO 8601, from the export>
messages: <count on the exported path>
project: "<viewer tag label>"        # when tagged, including the No Project sentinel (exported as its label)
branch_points: <N>                    # only when the conversation has branch points
thinking: included | omitted          # only when the conversation has thinking blocks
attachments: included | referenced    # only when the conversation has attachments
exported: <ISO 8601 date>
generator: Claude Export Viewer <version>
---

# <title>
```

Rationale for the conditional fields: `project` reflects operator triage work — the export
ZIP itself carries no project membership, so the viewer's tag is the only record and belongs
in a full export of the conversation (operator decision). `thinking`, `attachments`, and
`branch_points` are preserve-and-flag honesty: when the file omits or abbreviates something
the conversation has, the file says so.

### Message structure

- Each message under a heading: `## User` / `## Assistant` (the display-layer sender
  labels; unknown senders pass through raw, mirroring the `sender_label` rule).
- Message body text: **verbatim stored Markdown**, unescaped, unwrapped. Content headings
  will pollute the document outline; accepted cost (fidelity wins, decided).
- No per-message timestamps in v1.2 — conversation-level dates live in frontmatter.
  (Considered; deferred as a possible future toggle.)

### Non-text content blocks

All rendered as single-line labelled blockquotes unless stated, mirroring the reading view:

- **Thinking blocks** (only when the export-time toggle is on): a `> **[Thinking]**` label
  line — carrying the viewer's truncated/cut-off flag when the export marks one — followed
  by the thinking text as continued blockquote lines. Blockquoting visually separates
  reasoning from the conversation while keeping it readable Markdown.
- **Tool use / tool result**: `> **[Tool: <name>]** <result summary>` — one line per card,
  as the reading view shows.
- **Attachments**: a `> **[Attachment: <name>]**` label line followed by the attachment's
  extracted text as continued blockquote lines — or, when the export-time toggle is off,
  the label line alone with `— text not exported.` Attachments are the *user's own input*
  to the conversation, which the reading view already renders in full; the 1.3+ boundary
  protects Claude's tool/artifact outputs, not this.
- **Files**: the one-line notice — the export contains only the reference, never the body.
- **Artifacts**: `> **[Artifact: <title> (<type>)]** — content not exported.` Placeholder
  parity with the reading view's cards.
- **Unknown block types**: `> **[Unrecognised content block: <type>]**` — preserve-and-flag.

### Branch handling (decided for v1.2; explicitly provisional)

- **Single-conversation export captures the branch combination currently displayed** — pure
  WYSIWYG; the existing URL-branch machinery already defines "what you're looking at," so no
  new semantics are introduced.
- At each branch point on the exported path, an annotation — singular:
  `> **[Branch point]** 1 alternate response exists and is not included in this file.`
  Plural:
  `> **[Branch point]** <N> alternate responses exist and are not included in this file.`
- **Bulk export takes the default branch path** per conversation, with the same annotations.
- **Provisional status** (operator, 2026-07): this approach ships in v1.2, but the operator
  is deliberately reserving judgment until real-world use. A branch-selection control at
  export time (choosing which branch combination — or combinations — to export) is a
  sketched possible future enhancement. Reassess after v1.2 is lived with; do not build the
  selection UI speculatively.

### Mechanical notes

- File naming: `YYYY-MM-DD_<slug>.md` (created date; slug from the title or the synthesised
  preview for untitled conversations); on collision within a bulk export, append a short
  uuid fragment. Bulk archive name: `claude-conversations_YYYY-MM-DD.zip`, flat internal
  structure (folder-by-project considered; deferred).
- No fences are added around any content by the serializer, so fence-collision handling is
  not needed; content fences pass through verbatim.
- Canadian English in all user-facing strings, per house rule.

## Export-time options

Two checkboxes in v1.2:

- **Include thinking blocks** — **default off**. Rationale: reasoning is often the most
  sensitive layer of a conversation, and a flat file has no collapse — it's all full text;
  the cautious default suits a public tool, and the archival user ticks one box.
- **Include attachment text** — **default on**. When unticked, each attachment exports as
  its one-line reference only (`> **[Attachment: <name>]** — text not exported.`).
  Rationale: attachment text is the user's own input and belongs in a full export by
  default, but conversations carrying large pasted documents deserve a compact option
  (operator decision, 2026-07).

The thinking philosophy must carry to the print work: the current
print skeleton force-opens all collapsed sections, which prints thinking unconditionally —
the print pass in this version aligns print behaviour with this toggle's philosophy rather
than shipping that default.

## UI entry points (two, only)

- **Conversation detail page**: an Export control (with the thinking checkbox) downloading
  the displayed branch combination.
- **Conversations list**: an "Export selected" action in the existing multi-select action
  bar (same checkbox), producing the `.zip`. Filter-then-select already covers "export all
  of project X" — no separate export page, no export-everything button.
- The copy-whole-conversation candidate (clipboard delivery of the same single-conversation
  serialisation, minus frontmatter?) rides this design; decide its inclusion and exact shape
  in the implementation brief once the serializer exists.

## Privacy and audit posture

- Browser downloads only; no server-side writes outside the existing cache mechanics; no
  new sanitiser surface (no HTML is generated or parsed by the serializer).
- Thinking default-off is the privacy-relevant default and is test-pinned.
- The v1.2.5 delta audit item for this feature reduces to: verify no filesystem write paths
  exist, verify the thinking default, verify bulk zip contents match the selection.

## Discovery inputs required before the implementation brief

1. **Content-block census** of the real archive (the `scan_artifacts.py` pattern: stdlib,
   aggregate-only output, Session D privacy rules apply) — every distinct content-block
   type and sender value with counts, so the serializer's rendering table above is checked
   against reality rather than assumed. Any type the table doesn't cover gets a rendering
   decision before implementation, not during.
2. **Print-CSS assessment** — the `@media print` block at `static/style.css` is a
   self-described skeleton; verify what it actually does today, and scope the polish pass
   (including the thinking-block alignment above) as v1.2 work.

## Considered and deferred (do not build without reopening here)

PDF generation in-app (print-to-PDF path instead, per roadmap); tool/file/artifact bodies
(1.3+ extraction); per-message timestamps; other formats (HTML, JSON); full branch-tree
export and the branch-selection UI; folder-by-project zip structure; per-conversation
export from search results.
