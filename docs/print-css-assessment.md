# Print-CSS assessment (v1.2 Session L)

**Status**: discovery deliverable, Session L (2026-07). Current-state findings on the
`@media print` block at the bottom of `static/style.css` (the self-described skeleton), checked
against the actual templates, plus a scoped recommendation list for the v1.2 print-polish
session. **Nothing was implemented this session** — recommendations only, per the brief's hard
stop.

## Method

- Code-level read of the print block against every template (`base.html`, `_message.html`,
  `conversation_detail.html`, `_macros.html`, and the list/memory/help/search/project pages),
  cross-checked against the screen rules that compete with it in the cascade.
- Two empirical probes in the app's embedded Chromium engine (Chrome 148), run on the help and
  search pages (which render no export content): the print block's `<details>` rules were
  injected as screen rules — the mechanism under test is media-independent — and element
  geometry and computed styles measured before and after. No screenshots were taken or
  committed.

## Headline finding: the force-open hack no longer works

The skeleton's centrepiece is:

```css
details > summary { display: none; }
details > *:not(summary) { display: block !important; }
```

This was the classic "print collapsed sections expanded" hack, and it is dead in modern
engines. Chromium moved `<details>` rendering to a `::details-content` pseudo-element carrying
`content-visibility: hidden` while closed; an author `display` override on the children no
longer reveals them. Verified directly: with the rules applied, a closed help-page block's
content stayed unrendered (`::details-content` computes `content-visibility: hidden`; the
details box stayed at summary height). Firefox shipped the same `::details-content` model in
2025 — treat the hack's failure as the baseline for all evergreen browsers.

Meanwhile the companion rule — hide every `summary` — *half*-works, gated by cascade
accidents:

- `.thinking > summary`, `.attachments > summary`, and `.syntax-guide > summary` declare no
  `display`, so the type-selector rule hides them. Their closed `<details>` print as **empty
  husks**: a bare border-and-padding box (measured: ~15 px) with no label and no content.
- `.memory-block-summary`, `.memory-section-summary`'s siblings `.project-doc > summary`, and
  the help blocks' summaries declare `display: flex` at class specificity, which **beats** the
  print rule — those summaries still print (labels without bodies when closed).

So the skeleton's *intent* (print everything expanded) is not what happens anywhere, and what
does happen is inconsistent between block families.

## What the block does today, verified against the templates

**Hidden chrome** — the `display: none` list covers: site header/nav/footer, sort toggles,
tag filter, tag forms (including inside `.conv-row-tag` and `.conv-tag` wrappers), list
controls, bulk bar, row checkboxes, breadcrumbs, branch switchers, search form, all three
banners (orphan, load-error, inspect — the inspect exit button rides its banner), orphan purge
form, upload form, and the help page-level controls. Coverage is good. Two gaps:

- `.memory-block-controls` (the per-memory "Expand all sections / Collapse all sections"
  buttons) is not hidden — the buttons print inside any open memory or help block.
- `<noscript>` tag-form submit buttons are inside `.tag-form`, so they're covered; no other
  interactive chrome escapes the list.

**Message thread** — `.msg` cards flatten to a bottom-ruled continuous thread on white at
11 pt; the human/assistant left-border colour cue is removed (sender remains as the printed
`.msg-header` text). `.site-main` goes full-width.

**A printed conversation today** (describing, per the brief):

- Title, optional summary, and the meta line print — including the "N branch points" count and
  the message count "on this branch", which is the only surviving hint that branches exist.
- Each message prints sender plus a **relative** timestamp ("3 days ago") — absolute dates
  live only in `title` tooltips, which don't print.
- Text blocks print in full. Tool cards print as their one-line monospace card (tag + name);
  card backgrounds vanish under default print-backgrounds-off, but the text survives.
- **Thinking blocks print as unlabelled empty husks when closed** (the default), and as
  unlabelled reasoning text when the reader happened to open them on screen — the summary
  (the only "[Thinking]" label) is hidden either way. Print is accidentally WYSIWYG for
  thinking content, minus the labels, plus ghost boxes.
- **Attachments behave identically**: closed (the default) → empty bordered husk; open → full
  extracted text without the "N attachments" summary line (per-attachment filename headers do
  print).
- The `.files-note` binary-reference line prints. Unknown-block cards print.
- **Branch switchers are hidden entirely** — no annotation marks the branch points in the
  thread; alternates disappear without a trace beyond the header count.
- Links print as plain black text; URLs are lost (internal viewer links losing their targets
  is right; content links losing theirs is a fidelity cost).
- Long code lines are **clipped**: `.markdown-body pre` keeps `overflow-x: auto`, and print
  has no scrollbars. Syntax-highlight text colours print; the code background typically
  doesn't.

**Page-break rules** — `.msg`, `section`, and `pre` all carry `break-inside: avoid`.
For `.msg` and `pre` this is reasonable hygiene for typical sizes; elements taller than a page
can't honour it and split anyway (engines ignore unsatisfiable constraints, though some insert
a leading page break first, costing blank space). The `section` rule is effectively a no-op on
the pages that matter — `.conv-detail`, `.memory-page`, and `.search-page` are each one
full-page `<section>`, so the constraint is unsatisfiable by construction. `h1/h2/h3` get
`break-after: avoid` (fine).

**Other pages, briefly**: memory and help print block titles (those summaries win the
cascade) with bodies only for blocks open on screen; the project page's custom-instructions
`<details>` is bare — summary hidden, body unreachable — so **custom instructions vanish from
a printed project page entirely**, and knowledge-doc bodies print only if opened; the search
page prints results and snippets (`<mark>` highlights typically lose their background in
print) with an empty husk where the syntax guide sits.

## Recommendations for the print-polish session

Scoped list; each is a recommendation, not a decision. Items 1–2 carry the design record's
alignment requirement and should be treated as the core of the pass.

1. **Thinking alignment (required by `docs/export-design.md`): print must not force-open
   thinking blocks unconditionally.** Recommend deliberate WYSIWYG: drop both `<details>`
   rules (the dead force-open *and* the summary-hiding), letting closed blocks print as their
   summary line — an honest `Thinking (N chars)` marker — and open blocks print label plus
   body. The screen state becomes the print-time toggle, exactly parallel to the export
   feature's checkbox, and the empty-husk artefact disappears with the same edit. The current
   behaviour only *accidentally* avoids printing thinking (the platform broke the rule the
   skeleton wrote); the polish pass should make it deliberate.
2. **Do not resurrect blanket force-open via the modern mechanism**
   (`details::details-content { content-visibility: visible; }`). If force-open is ever wanted
   for specific non-sensitive sections (e.g. the help page), scope it per-section and record
   the decision; a blanket rule reintroduces the exact misalignment item 1 removes — and would
   now also print every closed memory block and attachment.
3. **Attachments follow item 1 automatically** and that is the right outcome: WYSIWYG with an
   honest summary line, matching the reading view.
4. **Wrap code in print**: `pre { white-space: pre-wrap; overflow-x: visible; }` so long lines
   wrap instead of clipping.
5. **Hide `.memory-block-controls` in print** (the expand/collapse buttons).
6. **Branch-point honesty (candidate)**: instead of hiding `.branch-switcher` wholesale,
   consider printing its label line ("Branch point — N sibling responses") while hiding the
   option list — parity with the export format's branch annotation. Operator call.
7. **Absolute timestamps (candidate, template change)**: printed pages carry only relative
   times; surfacing the absolute date in print needs the value in the DOM (e.g. a print-only
   element), which touches templates, not just CSS — flag for scoping.
8. **Prune the no-op `section` break rule**; keep `.msg`/`pre` break-avoidance, accepting that
   page-size elements split.
9. **Content-link URLs (candidate, operator taste)**: a
   `.markdown-body a[href]::after { content: " (" attr(href) ")" }`-style rule would preserve
   link targets in print at the cost of visual noise.
10. **Keep** the chrome hide-list (including the footer — it carries the account name and
    source path, which a shared printout shouldn't leak) and the flattened message styling;
    both do what the polish pass needs already.
