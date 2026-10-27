# Proposal — the three export/print toggles as one options pass

**Status: proposal for chat ratification. Nothing here is ratified.** The plan's 1.3.x
queue carries three toggles, each shipped so far as a fixed default: the **print
timestamp toggle** (None / Absolute / Relative — None shipped in Session N), the
**link-appendix mode toggle** (appendix / inline URLs / none — appendix shipped), and the
**summary-placement toggle** (metadata-only shipped in Session O: frontmatter when present,
so Copy omits it and Print keeps showing it).

## Problem

Designed one at a time, three toggles become three controls in three places with three
persistence stories. Designed together, they are one "output options" surface.

## Evidence from the code

- `viewer/export.py` `ExportOptions` holds the two export-time toggles (thinking off,
  attachment text on) — design-locked, test-pinned, passed as query or form values
  (checkbox + hidden-0 pairs, checkbox first; Session N).
- Print is pure CSS plus `static/print_links.js`; timestamps don't print (a print rule),
  the appendix is built client-side, and the summary prints because it is page content.
- Display preferences already persist as first-party HttpOnly cookies set by POST routes
  (`viewer_theme`, `viewer_legacy_memory` in `viewer/routes.py`), server-rendered — no
  `localStorage`.

## Options

| Option | Shape | Trade-offs |
|---|---|---|
| A. One "Print and export options" panel, cookie-persisted | A small `<details>` beside the existing Export options fieldset on the conversation page; three selects; one POST route writing three cookies (or one cookie with three fields) | One place to look; follows the theme cookie pattern; print options must reach the page as classes on `<html>`/`.conv-detail` for CSS to act on (server-rendered, no flash). |
| B. Per-action query parameters only | `?timestamps=absolute` on the print view; export already uses parameters | No persistence — users re-choose every time; print has no URL of its own (it's `window.print()`), so this needs a print-view route. |
| C. Ship each toggle separately as queued | Three small sessions | Least design up front; inconsistent placement likely. |

## Recommendation

**A**, with the two existing export checkboxes left where they are (their defaults are
design-locked and per-action), and the three new choices persisted: they describe how
*this reader* wants output, like the theme. Summary placement applies to Export and Copy
(it changes generated Markdown) and to print (WYSIWYG of the page), so its control sits in
the same panel.

## Combinations and extremes

| Case | Behaviour |
|---|---|
| Timestamps Absolute + print | Each message header prints its local timestamp |
| Timestamps Relative + print months later | Prints "3 months ago" frozen at print time — consider forbidding Relative for print, or label it |
| Appendix mode Inline + very long URLs | `pre`-style wrapping already applies in print; long URLs wrap |
| Appendix mode None + bare-URL autolinking (separate proposal) | Anchors print as underlined text only |
| Summary placement Body + Copy | Copy (frontmatter off) now includes the summary as a body line |
| Inspect mode | Options are display preferences: usable while inspecting, like the theme |
| Cookies cleared | Fixed defaults return (today's behaviour) |
| Export of a bulk selection | Uses the same persisted choices |

## Copy appendix (*proposed*)

- Panel summary (*proposed*): `Print and export options`
- Labels (*proposed*): `Timestamps when printing` — `None`, `Date and time`, `Relative`;
  `Links when printing` — `List at the end`, `After each link`, `Don't list`;
  `Conversation summary` — `In the file's metadata`, `At the top of the text`.
- Help, Appearance (*proposed*): "Your print and export choices are remembered the same
  way, in cookies on this machine. Nothing is sent anywhere."
