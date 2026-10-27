# Proposal — bare-URL autolinking (with the print links appendix)

**Status: proposal for chat ratification. Nothing here is ratified.** Queued in the plan's
1.3.x list since the v1.3.0 arc (record §11 of `format-support-design.md`); the operator
hand-test of 2026-09-01 observed plain-text links in replies.

## Problem

A URL Claude wrote without Markdown link syntax (`see https://example.invalid/page`)
renders as plain text: it isn't clickable on screen, and the print links appendix — which
numbers every anchor in a conversation — never lists it. Session O measured the effect on
a real archive: 91 of 213 conversations carried bare URLs and no Markdown-form link, so
their printouts have no appendix at all.

## Evidence from the code

- `viewer/render.py` `markdown_to_html`: python-markdown with `fenced_code`, `tables`,
  `sane_lists`, `nl2br` — no autolink extension — then `bleach.clean` with the allowlist.
  Bare URLs pass through as text.
- `static/print_links.js` collects `.markdown-body a[href]` only; text URLs are invisible
  to it.
- bleach's default protocols (`http`, `https`, `mailto`) already govern every anchor's
  `href`; `bleach.linkify` exists and honours the same protocol list.

## Options

| Option | How | Trade-offs |
|---|---|---|
| A. `bleach.linkify` after `clean` | One call in `markdown_to_html`; `skip_tags={"pre","code"}` so code stays literal | Smallest change; no allowlist change (it emits `<a href>`, already allowed); covers every Markdown surface at once (messages, docs, memory, help, artifact pages); linkify's URL grammar is conservative and may miss trailing-punctuation edge cases. |
| B. Markdown extension (e.g. a regex inline pattern) | Before sanitising | Integrates with Markdown parsing (respects code spans natively), but is a new dependency or custom code; same sanitiser result. |
| C. Print-only: teach `print_links.js` to find text URLs | Client-side text scan at print time | Screen behaviour unchanged (links still not clickable); fragile text walking; doesn't help the reading view. |
| D. Leave as is | — | Printouts of most real conversations keep no appendix. |

## Recommendation

**A**, with `skip_tags={"pre", "code"}` and `parse_email=False`, plus `rel="noopener
noreferrer nofollow"` added by a linkify callback (the attribute needs adding to the `a`
allowlist entry — `rel` only — which **is** a sanitiser-allowlist change and therefore a
hard-stop item for the implementing session to bring back with a brief; without the `rel`
change, A ships with the same attributes every existing anchor has). The print appendix
then picks bare URLs up with no JavaScript change.

## Combinations and extremes

| Case | Behaviour under A |
|---|---|
| URL inside a fenced code block or inline code | Untouched (skip tags) |
| URL already in Markdown link form | Unchanged — linkify skips existing anchors |
| Search snippet with a `<mark>` inside a URL | Snippets go through `markdown_to_html` too; linkify must run *after* the sentinel substitution, and a URL broken by `<mark>` may link only partly — pin with a test |
| `javascript:` or `data:` text | Not linked (protocol list) |
| Trailing punctuation (`https://x.invalid/a).`) | Linkify trims common trailing punctuation; edge cases pinned by tests |
| Thousands of URLs in one document | Linear pass; no measurable cost against the existing pipeline |
| Print appendix | Lists the new anchors automatically, numbering shared by URL |
| Export to Markdown | Unaffected — the serializer writes stored Markdown, not HTML |
| Artifact `page` Markdown and Help | Linked too (same pipeline) — Help's own links are already Markdown form |

## Copy appendix (*proposed*)

- Help, Reading a conversation (*proposed*): "Web addresses in messages are clickable,
  even when Claude didn't format them as links, and printouts list them in the links
  appendix."
- Release-notes line (*proposed*): "Plain web addresses in conversations are now
  clickable and appear in the print links appendix."
