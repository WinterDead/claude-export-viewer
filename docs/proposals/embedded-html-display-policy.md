# Proposal — embedded-HTML display policy

**Status: proposal for chat ratification. Nothing here is ratified, and this proposal
changes nothing** — any change it recommends is a sanitiser-allowlist decision, which the
project treats as a hard stop requiring its own written brief.

## Problem

When a message contains literal HTML — a `<details>` block Claude wrote, an inline
`<span>`, a pasted snippet outside a code fence — the viewer silently drops the tags and
keeps their inner text. Characters the author wrote vanish from the reading view and from
print, and the reader can't tell anything was removed. (Queued in the plan's "Later" list.)

## Evidence from the code

- `viewer/render.py` `markdown_to_html`: python-markdown passes raw HTML through, then
  `bleach.clean(..., strip=True)` removes every tag not in `_ALLOWED_TAGS` (keeping inner
  text). `strip=False` would instead escape disallowed tags to visible text.
- The allowlist is the app's sanitisation boundary (comments on `_ALLOWED_TAGS` and
  `_CODE_CLASS_RE`; plan v1.1 Session F): no widening without a brief.
- The conversation export (`viewer/export.py`) writes the stored Markdown untouched, so
  exported files already keep the author's characters; only the rendered views lose them.

## Options

| Option | Effect | Trade-offs |
|---|---|---|
| A. Escape instead of strip (`strip=False`) | `<details>` shows as the literal text `<details>` | Nothing the author wrote disappears; no new tags allowed; the page shows raw markup some readers won't expect; one flag, all surfaces at once. |
| B. A + real collapsibles for well-formed `<details>`/`<summary>` | Add `details`/`summary` to the allowlist, no attributes | Matches what the author intended; widens the allowlist (two tags, zero attributes) — inert elements, no script surface; malformed blocks fall back to A's escaping only if detected, otherwise browsers auto-close. |
| C. Strip, but mark the loss | Replace each stripped tag with a small marker | Honest about the loss, but invents copy inside user content and still hides the characters. |
| D. Keep stripping | Status quo | Silent loss of author characters. |

## Recommendation

**A** as the display default — the smallest change that stops silent loss, with no new
tags. Treat **B** as a separate, later decision once A has been lived with, since it is the
only option that widens the allowlist. Either way the implementing session needs its own
brief because `markdown_to_html` is the boundary.

## Combinations and extremes

| Case | A | B |
|---|---|---|
| `<script>alert(1)</script>` in a message | Shown as text; never executes | Same (script never allowed) |
| `<details><summary>x</summary>y</details>` | Literal tags visible around x and y | A working collapsible |
| Unclosed `<details>` | Literal tag visible | Browser auto-closes at the end of the block — content after it may fold unexpectedly; mitigate with an html5lib balance check or fall back to A |
| HTML inside a fenced code block | Unchanged (already escaped by Markdown) | Unchanged |
| `<mark>` in search snippets | Unchanged (allowed tag) | Unchanged |
| Artifact `page` Markdown | Same pipeline, same result | Same |
| Print | Literal text prints | Collapsibles follow the WYSIWYG rule (closed prints its summary) |
| Help page (authored Markdown) | No literal HTML in it today; unaffected | Unaffected |

## Copy appendix (*proposed*)

- Help, Reading a conversation (*proposed*, A): "If a message contains HTML that wasn't in
  a code block, the viewer shows it as plain text rather than running or hiding it."
