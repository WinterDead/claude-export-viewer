# Help

This app is a local viewer for your Claude data export — the ZIP archive Anthropic provides when you request your data. It parses the archive on your machine and gives you a browsable, searchable interface over your conversations, projects, and memory. Everything runs locally: your export is never uploaded anywhere, and nothing leaves your computer.

The sections below cover everything from requesting an export to advanced tagging workflows. Click any heading to expand it.

## Getting your export from Anthropic

In Claude (claude.ai or the desktop app), click your initials in the lower-left corner, open **Settings**, go to the **Privacy** section, and click **Export data**. Anthropic processes the request and emails you a download link; the link expires after 24 hours, so download promptly. The download is a ZIP archive — that ZIP, exactly as downloaded, is what this viewer reads. Don't unzip it.

If you're on a Claude Team or Enterprise plan, individual members can't self-export; the organisation owner has to run an export from Organization settings.

The export contains your conversations (including messages on alternate branches), your projects with their instructions and knowledge documents, and Claude's memory. It does **not** contain uploaded file bodies (only references to them), and it does not record which project each conversation belonged to — which is why this viewer has its own tagging system (see *Tagging and the triage workflow*).

## Loading an export into the viewer

Click **Upload** in the top nav, choose your export ZIP, and upload. The viewer parses and validates the archive before replacing anything — if the file is corrupted or has an unexpected structure, you get an error and whatever was previously loaded stays intact. Uploads are capped at 200 MB, which comfortably covers any realistic export.

On startup, the app loads the most recently uploaded export automatically. If you've never uploaded one, it shows a small built-in sample dataset so the interface is explorable — the footer always shows which source is loaded, so you can tell sample data from your own.

Each successful load is recorded on the [History](/history) page.

## The home page

A dashboard with two parts:

- **Stats panel** — totals for conversations, messages, branched conversations, projects, and project memories. The quickest way to confirm the right export is loaded.
- **Global memory** — Claude's accumulated memory about you, collapsed by default. Expand it to read the full text, with its internal sections as nested collapsible blocks.

## Browsing conversations

The **Conversations** page is the main entry point. Each row shows the title (or a preview synthesised from the first message, for untitled conversations), the auto-generated summary if there is one, the message count, when it was last active, a branch callout if the conversation has divergent branches, and a tag dropdown.

### Sorting

Three toggles at the top: Recently updated (the default), Recently created, and Title (A–Z).

### Filtering

A filter dropdown narrows the list by tag state: All conversations, Untagged (not yet reviewed), No Project (reviewed, belongs to no project), or any of your projects. See *Tagging and the triage workflow* for how these states fit together.

### Selecting multiple conversations

Click a row's checkbox to start a selection; an action bar appears. Shift-click selects a range. The bar adapts to what you've selected — for example, the Retag action only appears when the selection includes already-tagged rows. From the bar you can tag the selection to a project (or to No Project), retag, or untag in one action.

## Reading a conversation

The full reading view. Messages are labelled **User** and **Assistant** and visually distinguished.

### Message types and content blocks

- **Text** renders as formatted markdown.
- **Thinking blocks** (Claude's internal reasoning, where present) are collapsed by default with a size summary — expand to read.
- **Tool use and tool results** render as labelled cards showing the tool name and result summary.
- **Attachments** are folded into expandable blocks with their extracted text inside.
- **Files** show a one-line notice — the export contains only the reference, never the file body.

### Branches

When a message was regenerated or edited in the original conversation, the conversation has branch points. A switcher appears at each one, showing the sibling responses; the currently shown branch is marked. Clicking a sibling reloads the page on that branch — the URL captures your branch choices, so a link to a specific branch combination can be bookmarked or shared between your own notes. Search results that match a message on an alternate branch link straight to that branch.

## Projects

The **Projects** page lists every project in the export. Each project's detail page shows its description, its custom instructions, its knowledge documents (rendered in full), its project-specific memory, and the conversations you've tagged into it. Project pages are read-only — tagging happens from the conversations list.

## Memory

The **Memory** page collects every memory blob in the export: global memory first, then per-project memories. Everything is collapsed by default; expand a memory to see its internal sections as nested collapsible blocks, with per-memory expand/collapse-all controls. If a memory belongs to a project that no longer exists in the export, it's surfaced in its own section at the bottom rather than hidden.

## Search

Search runs across everything indexed: conversation titles and summaries, every message (including alternate branches), project names and descriptions, knowledge documents, and all memory.

Just type naturally — punctuation, apostrophes, and hyphens are handled for you. For exact phrases, prefix matching, proximity, and boolean operators, open the **Search syntax guide** on the [search page](/search) itself.

Results are grouped by source and capped at 200 hits — if a query would return more, refine it. Each result links back to its exact location: message hits scroll you to the matching message, branch hits open the right branch, memory hits open the right section.

## Tagging and the triage workflow

Anthropic's export doesn't record which project a conversation belonged to, so the viewer maintains its own tags in a local database. Tags persist across re-imports — upload a newer export and your tagging work carries over.

### The three states

Every conversation is in exactly one of:

- **Untagged** — not yet reviewed. Every conversation starts here, and new conversations from a re-import arrive here.
- **Tagged to a project** — you've assigned it.
- **No Project** — you've reviewed it and confirmed it belongs to no project.

The distinction between Untagged and No Project is what makes re-imports manageable: Untagged always means "still to review."

### The initial sweep

The efficient pattern for a freshly loaded export:

1. Open the [Untagged filter](/conversations?tag=untagged) — this is your working list.
2. Pick a project. Select the conversations that belong to it (shift-click for ranges) and bulk-tag them. Tagged rows drop out of the Untagged view as you go.
3. Repeat for each project.
4. Tag everything left over — the conversations that genuinely belong to no project — to **No Project**.

When the Untagged view is empty, the sweep is done, and it stays done: from now on, anything Untagged is new.

### After a re-import

Upload the newer export. Conversations you already triaged keep their tags; only genuinely new conversations appear Untagged. Review those, and you're current again. If the new export is missing something a tag pointed at, the viewer tells you — see *Orphans*.

### One-off corrections

For a single conversation, use the tag dropdown on its row in the conversations list or on its detail page — faster than the bulk flow.

## Orphans

After a re-import, some tags may point at things that no longer exist — a conversation that was deleted, or a project that's gone while its conversations remain. The viewer never silently discards these: a banner appears, and the **Orphans** page sorts them into two buckets — orphaned conversations (the conversation itself is gone) and orphaned project tags on conversations that still exist (those can be retagged from the conversation's dropdown). When you're sure you don't need them, the page has an explicit-confirm purge. The purge is irreversible, so consider backing up first (see *Backups and your data*).

## Upload history

The [History](/history) page records every export load — when, whether it came from an upload or app startup, the filename, a content fingerprint, and the headline counts at the time. Over multiple re-imports it doubles as a record of how your archive has grown. Restarting the app with the same export doesn't add duplicate entries; uploading through the UI always records, even for a file you've loaded before.

## Backups and your data

Everything this app knows lives in its local `cache/` folder, and two files there are worth protecting:

- **`viewer.sqlite`** — your tagging work and upload history. Losing it means redoing the triage sweep.
- **`active_export.zip`** — the currently loaded export.

Back them up **together**: tags reference conversation IDs in the export, so restoring one without the other can produce orphans. To restore, stop the app, copy both files back into `cache/`, and restart.

A privacy note on destinations: the export ZIP contains your complete conversation history, so it's the most sensitive file on this list. A local or encrypted backup destination is the cautious choice for it; cloud-synced folders upload whatever you place in them. The tag database reveals far less (titles and groupings, not content), and the export itself can always be re-requested from Anthropic, so treating the ZIP as replaceable rather than backing it up is also a reasonable approach.

## Troubleshooting

### I see sample data instead of my export

No export has been uploaded yet. Upload your ZIP via the Upload page. The footer always shows what's currently loaded.

### The app fails to start with a parse error

The stored export is malformed. Check the startup log in the terminal for specifics. Because the app can't start, the Upload page isn't reachable — delete (or move) cache/active_export.zip, start the app, and upload a good export through the Upload page.

### Search finds nothing for something I know exists

Check whether the query is accidentally strict — an `"exact phrase"` requires those words adjacent and in order; try without quotes. Also confirm the content is in an indexed surface (see *Search* for the list).

### Search shows a syntax error

The query contains operator syntax with a mistake — an unclosed quote, mismatched parenthesis, or punctuation in strict mode. Simplest fix: remove the operator characters and let natural-language handling take over. The syntax guide on the search page covers the rules.

### My tags disappeared after a re-import

They almost certainly didn't — they've gone orphaned because the new export is missing what they pointed at. A banner should be visible; the **Orphans** page explains what's recoverable.

### Port 5000 is already in use

Something else on your machine is using the default port. Start the app on another one: `flask --app app run --port 5050`.

### A page shows stale content

Hard-refresh the browser (Ctrl+F5 / Cmd+Shift+R), and if that doesn't do it, restart the app.
