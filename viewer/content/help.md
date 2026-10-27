# Help

This app is a local viewer for your Claude data export — the files Anthropic provides when you request your data. It parses the export on your machine and gives you a browsable, searchable interface over your conversations, projects, memory, account data, published artifacts, and Claude Code scheduled tasks. Everything runs locally: your export is never uploaded anywhere, and nothing leaves your computer.

The sections below cover everything from requesting an export to advanced tagging workflows. Click any heading to expand it.

## Getting your export from Anthropic

In Claude (claude.ai or the desktop app), click your initials in the lower-left corner, open **Settings**, go to the **Privacy** section, and click **Export data**. Anthropic processes the request and emails you a download link; the link expires after 24 hours, so download promptly. Current exports arrive as a small manifest file listing several ZIP files (conversations, projects, memories, account metadata, and — if you use them — published artifacts and Claude Code scheduled tasks); download them all and upload them to the viewer together, exactly as downloaded — there's no need to unzip anything. If all you have is the manifest file, the [Download links](/manifest) page turns it into one-click links. Older single-ZIP exports load exactly as before.

If you're on a Claude Team or Enterprise plan, individual members can't self-export; the organisation owner has to run an export from Organization settings.

The export contains your conversations (including messages on alternate branches), your projects with their instructions and knowledge documents, Claude's memory, and — in current exports — your account details and login history (see *The Account page*). It does **not** contain uploaded file bodies (only references to them), and it does not record which project each conversation belonged to — which is why this viewer has its own tagging system (see *Tagging and the triage workflow*).

## Loading an export into the viewer

Choose your export files and upload them together — every ZIP from a current export plus, optionally, the manifest, or a single legacy ZIP. You can load a partial set; a banner will tell you what's missing and which features are affected. A file the viewer doesn't recognise is skipped with a notice, and the rest of your export loads.

Your manifest file contains live, signed download links to your export. Treat it like the export itself: don't share it, post it, or commit it anywhere public.

Uploads are capped at 200 MB per file — more than any export file seen so far. If yours ever exceeds it, set the VIEWER_MAX_UPLOAD_MB environment variable before launching to raise the cap.

The viewer parses and validates the files before replacing anything — if a file is corrupted or has an unexpected structure, you get an error and whatever was previously loaded stays intact.

On startup, the app loads the most recently uploaded export automatically. If you've never uploaded one, it shows a small built-in sample dataset so the interface is explorable — the footer always shows which source is loaded, so you can tell sample data from your own.

Each successful load is recorded on the [History](/history) page.

## Inspecting another export

Sometimes you want to look inside an export without making it yours — a second account's archive, a colleague's export, or a test file. The [Inspect page](/inspect) opens an export temporarily and read-only: no tagging, nothing saved to disk, nothing recorded in upload history, and your own export and tags stay exactly as they were. A banner reminds you which file you're inspecting; the **Return to my export** button in the banner (or simply restarting the app) puts everything back. To replace your stored export instead, use Upload.

## The home page

A dashboard with two parts:

- **Stats panel** — totals for conversations, messages, branched conversations, projects, and project memories. The quickest way to confirm the right export is loaded.
- **Conversation memory** — Claude's accumulated memory about you, collapsed by default. Expand it to read the full text, with its internal sections as nested collapsible blocks.

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

- **Text** renders as formatted markdown. Where a current export records the web sources a passage cited, a small **Sources** list appears beneath the paragraph.
- **Thinking blocks** (Claude's internal reasoning, where present) are collapsed by default — teased by Claude's own one-line summary when the export provides one, or by a size summary otherwise. Expand to read the reasoning, with the full summary list above it. Blocks the export marks truncated or cut off say so, and a block hidden in the Claude app carries a small **Hidden in the app** badge — the viewer shows everything in your export.
- **Tool use and tool results** render as labelled cards showing the tool name. Where a current export carries the app's own display content, the card shows it — tables as tables, links as links, code as highlighted code — and anything structured beyond that folds into a collapsed section that expands to formatted JSON. Long display content (over 30 lines or 2,000 characters) starts collapsed, labelled with the tool's own status line and its line count.
- **Injected prompts** (text the product added to a turn — a skill or feature preamble) appear as collapsible **Injected prompt** cards, collapsed by default.
- **Documents** you attached to a message show as a one-line **Document** card with the document's name — the file itself isn't in the export.
- **Attachments** are folded into expandable blocks with their extracted text inside.
- **Files** show a one-line notice — the export contains only the reference, never the file body.

### Branches

When a message was regenerated or edited in the original conversation, the conversation has branch points. A switcher appears at each one, showing the sibling responses; the currently shown branch is marked. Clicking a sibling reloads the page on that branch — the URL captures your branch choices, so a link to a specific branch combination can be bookmarked or shared between your own notes. Search results that match a message on an alternate branch link straight to that branch.

## Exporting conversations

Any conversation can be downloaded as a Markdown file — the message text exactly as stored, with a small metadata header (title, dates, summary, message count, your project tag if you've set one) and clearly labelled markers for everything else: tool activity, attachments, branch points, and anything not included.

### Single conversations

On a conversation's page, use **Export**. The file captures the branch combination you're currently viewing — switch branches first if you want a different path. Two checkboxes control the contents: **Include thinking blocks** (off by default — Claude's reasoning is often the most sensitive part of a conversation) and **Include attachment text** (on by default; untick it to keep attachments as one-line references). Attachment text means text-based attachments; binary files (images, PDFs, and so on) are never included in any export — Anthropic's archive stores only references to them.

### Exporting a selection

In the conversations list, select rows the usual way and choose **Export selected** from the action bar: you get a ZIP with one Markdown file per conversation, each following its default branch path. The filter makes short work of "everything in project X" — filter, select, export.

### Copying instead of downloading

**Copy conversation** on a conversation's page puts the same Markdown on your clipboard (without the metadata header) — handy for pasting into notes or another chat.

### What the file admits

Exports are honest about omissions: if thinking was left out, the header says so; attachments reduced to references say so; and every branch point on the exported path is marked with how many alternate responses exist. An export is the conversation as a document, not a copy of the whole tree.

### Printing and PDF

**Print / Save as PDF** on a conversation's page opens your browser's print dialog — choose "Save as PDF" there for a PDF copy. Printing is what-you-see-is-what-you-get: collapsed sections (like thinking blocks) print as their one-line summaries unless you've expanded them, and a Links section at the end lists the full address of every link in the conversation.

## Projects

The **Projects** page lists every project in the export. Each project's detail page shows its description, its custom instructions, its knowledge documents (rendered in full), its project-specific memory, and the conversations you've tagged into it. Project pages are read-only — tagging happens from the conversations list.

## Memory

The **Memory** page collects every memory in the export, in up to three groups — whichever your archive contains:

- **Conversation memory** — the running memory Claude builds across your conversations, with its internal sections as nested collapsible blocks and per-memory expand/collapse-all controls.
- **Memory files** — the newer memory system's individual files (current exports only), grouped by their folder paths. Each file is collapsed under its full path with its last-updated date; files that begin with a small `---` metadata header show that header verbatim above the rendered body.
- **Project memories** — per-project memory, sortable like the other lists. If a memory belongs to a project that isn't in the export, it's surfaced in its own section rather than hidden.

Everything is collapsed by default — click a header to expand it.

When your export includes memory files, the older memory summary is shown last, collapsed, and marked as no longer updated. To hide it from the Memory page and search, turn on Hide legacy memory. Exports without memory files always show their summary, and Inspect mode decides for each export it opens.

## The Account page

Shown whenever the export includes your account file — the [Account](/account) page displays the identity data in your archive (name, email, and account ID) and, for current exports, your **login history**: a table of recorded logins, newest first, with the timestamp (in your local time), method, IP address, location, and browser and operating system. Single-ZIP exports include login history only when the archive carries it (older ones don't); when it's absent, the page says so. It's your data — the viewer shows everything the archive contains.

## Published artifacts

If your export includes published artifacts, the [Artifacts](/artifacts) page lists each one with its type, its number of versions, and when it was last updated. An artifact's page shows its description and its versions — the active version is marked and opens by default — and then the content itself: a preview for an **Artifact** (a web page), or the text for a **Document**.

The preview is a safe copy of the page: the viewer doesn't run its scripts and doesn't load anything from the web, so an interactive artifact may look incomplete. Fonts bundled with an artifact aren't loaded either, so its text uses your system's fonts. The written content of a script-driven artifact is listed under **Content files**, where each file expands in place. **View source** shows the page's HTML, and **Download** saves the version: the page itself, or a ZIP of its files at their original paths (a Document downloads as its text).

Below the content come the version's files, each checked against the export's own record; any comments on a document; any data the artifact saved, grouped by collection; and the conversations that mention it. In those conversations, the tool card links back with **Open in Artifacts**. Saved data whose artifact isn't in your export is listed at the end of the Artifacts page.

## Scheduled tasks

Claude Code scheduled tasks (called routines in the export files) are listed with their prompts, schedules, and run history. The [Scheduled tasks](/scheduled-tasks) page shows each task's schedule, whether it's enabled, when it was created, and when it last ran; expand **Prompt** to read what it asks Claude to do. The export doesn't include the sessions themselves, so each run shows its session ID as text. Runs for tasks that aren't in your export are kept in their own section.

## Search

Search runs across everything indexed: conversation titles and summaries, every message (including alternate branches), project names and descriptions, knowledge documents, all memory — the conversation memory, memory files, and project memories — and published artifacts and scheduled tasks.

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

## Appearance

The viewer follows your system's light or dark preference by default. Use the Theme control at the end of the navigation bar to choose Light or Dark instead; the choice is remembered on this machine in a small cookie the app sets for itself — nothing is sent anywhere. Printing and PDF output always use the light theme.

The Hide legacy memory choice is remembered the same way, in a cookie named viewer_legacy_memory. Nothing is sent anywhere.

## Backups and your data

Everything this app knows lives in its local `cache/` folder, and two things there are worth protecting:

- **`viewer.sqlite`** — your tagging work and upload history. Losing it means redoing the triage sweep.
- **the `active_export/` folder** — the currently loaded export files, exactly as you uploaded them. (Uploads from older versions of the viewer may instead be a single `active_export.zip`; protect whichever exists.)

Back them up **together**: tags reference conversation IDs in the export, so restoring one without the other can produce orphans. To restore, stop the app, copy both back into `cache/`, and restart.

A privacy note on destinations: the export files contain your complete conversation history, so they're the most sensitive items on this list. A local or encrypted backup destination is the cautious choice; cloud-synced folders upload whatever you place in them. The tag database reveals far less (titles and groupings, not content), and the export itself can always be re-requested from Anthropic, so treating the export files as replaceable rather than backing them up is also a reasonable approach.

## What the viewer doesn't keep

Thinking blocks in current-format exports carry a verification signature — an opaque cryptographic blob with no human-readable content. The viewer doesn't display, store, or cache signatures; dropping them keeps the local cache smaller. A few other technical fields (per-block timing, search-result link lists) are preserved but not yet displayed. If you have a use for any of these, open an issue on GitHub.

The viewer never contacts the internet, so an image in a conversation or document that points to a web address isn't loaded. It shows as a broken image or its description instead.

## Troubleshooting

### I see sample data instead of my export

Two possible causes. If you've never uploaded an export, the app shows its built-in sample so the interface is explorable — upload your ZIP via the Upload page. If you have uploaded one, the stored export may have failed to load; a banner will say so (see the next entry). Either way, the footer always shows what's currently loaded.

### A banner says the stored export couldn't be loaded

The stored export is malformed. The app leaves the bad file in place and falls back to the next available source (usually the sample data), so nothing is lost — but your data isn't shown until the file is replaced. Upload a good export via the Upload page; a successful upload replaces the stored file and clears the banner. The terminal log has the parse details if you want them.

### Search finds nothing for something I know exists

Check whether the query is accidentally strict — an `"exact phrase"` requires those words adjacent and in order; try without quotes. Also confirm the content is in an indexed surface (see *Search* for the list).

### Search shows a syntax error

The query contains operator syntax with a mistake — an unclosed quote, mismatched parenthesis, or punctuation in strict mode. Simplest fix: remove the operator characters and let natural-language handling take over. The syntax guide on the search page covers the rules.

### My tags disappeared after a re-import

They almost certainly didn't — they've gone orphaned because the new export is missing what they pointed at. A banner should be visible; the **Orphans** page explains what's recoverable.

### Port 5000 is already in use

Something else on your machine is using the default port. The launcher script handles this automatically — it picks the next free port and prints the address. If you start the app manually instead, choose another port: `flask --app app run --port 5050`.

### The terminal warns "This is a development server"

That message comes from Flask, the web framework the viewer is built on, and it's expected. The warning matters for public websites; this app only listens on your own machine (127.0.0.1) and is never exposed to the network. Nothing needs fixing.

### A page shows stale content

Hard-refresh the browser (Ctrl+F5 / Cmd+Shift+R), and if that doesn't do it, restart the app.
