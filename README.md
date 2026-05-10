# Claude Export Viewer

A local Python web app for browsing Claude.ai data exports. Reads the ZIP that Anthropic sends when you request your data and renders conversations, project-grouped chats, and the memory file in a clean, searchable interface. Runs entirely on your own machine — nothing is uploaded anywhere.

## Why this exists

`osteele/claude-chat-viewer` does most of what's needed but ignores conversations that lived inside Claude Projects (treats them as ungrouped) and ignores the memory file entirely. This project fills those gaps.

## Quick start

> The application code is being scaffolded by Claude Code. This section will be replaced once the first working version exists.

```powershell
# placeholder — actual commands to be filled in by Claude Code after first scaffold
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
flask --app app run --debug
```

## Project layout

See `CLAUDE.md` for the proposed layout and `docs/export-schema.md` (created during the first session) for the export format reference.

## License

Personal project. No license declared.
