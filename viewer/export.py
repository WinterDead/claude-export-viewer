"""Serialise conversations to Markdown documents (v1.2 Session M).

Implements the format specification in ``docs/export-design.md``: YAML
frontmatter with conditional fields, an H1 title, sender headings via the
display-layer mapping, verbatim body text, and one-line labelled blockquotes
for everything the file omits or abbreviates. The serializer assembles the
*stored* Markdown — the markdown → bleach rendering pipeline is never
involved, so this module adds no sanitiser surface.

Pure functions with no Flask coupling (same rule as :mod:`viewer.render`), so
the serializer can be unit-tested without booting an app. The routes layer
owns branch-path resolution, tag-label lookup, and HTTP delivery.
"""

from __future__ import annotations

import importlib.metadata
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from viewer.parser import (
    Attachment,
    ContentBlock,
    Conversation,
    DocumentBlock,
    FileRef,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from viewer.render import conversation_display_title, sender_label, slugify

GENERATOR_NAME = "Claude Export Viewer"


def _load_version() -> str:
    """The app version for the ``generator`` frontmatter field.

    ``pyproject.toml`` is the single source of truth. Installed-package
    metadata is deliberately the *fallback*, not the primary: with an
    editable install (the launchers run ``pip install -e .``) the recorded
    metadata version is frozen at install time and goes stale when
    ``pyproject.toml`` is bumped without a reinstall.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        with pyproject.open("rb") as fh:
            version = tomllib.load(fh)["project"]["version"]
        if isinstance(version, str) and version:
            return version
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        pass
    try:
        return importlib.metadata.version("claude-export-viewer")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


APP_VERSION = _load_version()


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """The two export-time toggles. Defaults are design-locked and
    test-pinned: thinking off (the privacy-relevant default), attachment
    text on (the user's own input belongs in a full export)."""

    include_thinking: bool = False
    include_attachment_text: bool = True


# ---------------------------------------------------------------------------
# File naming.
# ---------------------------------------------------------------------------


def export_filename(conv: Conversation, *, fragment: str | None = None) -> str:
    """``YYYY-MM-DD_<slug>.md`` per the naming spec.

    The date is the conversation's created date as stored (UTC) — the same
    instant the frontmatter ``created`` field carries, so the filename and
    the file never disagree. The slug comes from the display title, which
    for untitled conversations is the synthesised preview.

    ``fragment`` is the bulk-export collision disambiguator (a short piece
    of the conversation uuid, chosen by the caller).
    """
    date_part = conv.created_at.date().isoformat()
    slug = slugify(conversation_display_title(conv))
    if fragment:
        return f"{date_part}_{slug}_{fragment}.md"
    return f"{date_part}_{slug}.md"


def bulk_zip_filename(exported_on: date) -> str:
    """``claude-conversations_YYYY-MM-DD.zip`` per the naming spec."""
    return f"claude-conversations_{exported_on.isoformat()}.zip"


# ---------------------------------------------------------------------------
# Serialisation.
# ---------------------------------------------------------------------------


def serialise_conversation(
    conv: Conversation,
    path_uuids: Sequence[str],
    options: ExportOptions | None = None,
    *,
    project_label: str | None = None,
    exported_on: date | None = None,
    include_frontmatter: bool = True,
) -> str:
    """Render one conversation path as a Markdown document.

    ``path_uuids`` is the message chain to export — the displayed branch
    combination for a single-conversation export (WYSIWYG), the canonical
    path for bulk. ``project_label`` is the resolved viewer-tag label, or
    ``None`` when the ``project`` frontmatter field should be absent.
    ``include_frontmatter=False`` (Session N — the clipboard delivery of the
    copy control) drops the YAML header; the H1 title stays either way.
    """
    if options is None:
        options = ExportOptions()
    if exported_on is None:
        exported_on = date.today()

    msg_by_uuid = {m.uuid: m for m in conv.messages}
    path_messages = [msg_by_uuid[u] for u in path_uuids if u in msg_by_uuid]
    title = conversation_display_title(conv)

    # Conditional-field triggers are conversation-scoped (all branches), per
    # the design record's wording — "when the conversation has" — and its
    # honesty rationale: the field flags what the *conversation* has that the
    # file may omit. Only ``messages`` is path-scoped, and says so in the spec.
    has_thinking = any(
        isinstance(block, ThinkingBlock) for msg in conv.messages for block in msg.content
    )
    has_attachments = any(msg.attachments for msg in conv.messages)

    segments: list[str] = []
    if include_frontmatter:
        fm = ["---"]
        fm.append(f"title: {_yaml_quote(title)}")
        # Summary rides as metadata only (v1.2.5 Session O, operator-ratified):
        # present in the frontmatter when the conversation has one, so Export
        # carries it, Copy (frontmatter off) omits it, and Print keeps showing
        # it on the page. The double-quoted scalar is the YAML-safe form for
        # multi-line text (_yaml_quote escapes the newlines).
        if conv.summary:
            fm.append(f"summary: {_yaml_quote(conv.summary)}")
        fm.append(f"uuid: {conv.uuid}")
        fm.append(f"created: {conv.created_at.isoformat()}")
        fm.append(f"updated: {conv.updated_at.isoformat()}")
        fm.append(f"messages: {len(path_messages)}")
        if project_label is not None:
            fm.append(f"project: {_yaml_quote(project_label)}")
        if conv.branch_points:
            fm.append(f"branch_points: {len(conv.branch_points)}")
        if has_thinking:
            fm.append(f"thinking: {'included' if options.include_thinking else 'omitted'}")
        if has_attachments:
            fm.append(
                f"attachments: {'included' if options.include_attachment_text else 'referenced'}"
            )
        fm.append(f"exported: {exported_on.isoformat()}")
        fm.append(f"generator: {GENERATOR_NAME} {APP_VERSION}")
        fm.append("---")
        segments.append("\n".join(fm))
    segments.append(f"# {_single_line(title)}")

    branch_point_set = set(conv.branch_points)
    for msg in path_messages:
        segments.append(f"## {sender_label(msg.sender)}")
        segments.extend(_render_blocks(msg.content, options))
        for att in msg.attachments:
            segments.append(_attachment_segment(att, options.include_attachment_text))
        if msg.files:
            segments.append(_files_segment(msg.files))
        if msg.uuid in branch_point_set:
            # Same sibling rule as the detail page's switcher: children that
            # resolve to real messages. One of them is the path we followed;
            # the rest are the alternates the file does not include.
            siblings = [c for c in conv.children_by_uuid.get(msg.uuid, []) if c in msg_by_uuid]
            if len(siblings) > 1:
                segments.append(_branch_annotation(len(siblings) - 1))

    return "\n\n".join(segments) + "\n"


# ---------------------------------------------------------------------------
# Content blocks.
# ---------------------------------------------------------------------------


def _render_blocks(blocks: list[ContentBlock], options: ExportOptions) -> list[str]:
    """Render a message's content blocks to document segments.

    Tool pairing is positional, per the Session D finding (``tool_use``/
    ``tool_result`` ids are null in the export; the result directly follows
    its call): a ``tool_use`` consumes an immediately following
    ``tool_result`` into one card, and tolerates having none (the census
    found two such interrupted calls in the real archive).
    """
    segments: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if isinstance(block, TextBlock):
            if block.text:
                segments.append(block.text)
        elif isinstance(block, ThinkingBlock):
            if options.include_thinking:
                segments.append(_thinking_segment(block))
        elif isinstance(block, ToolUseBlock):
            result = None
            if i + 1 < len(blocks) and isinstance(blocks[i + 1], ToolResultBlock):
                result = blocks[i + 1]
                i += 1
            segments.append(_tool_card(block, result))
        elif isinstance(block, ToolResultBlock):
            # A result with no preceding call — none exist in the real
            # archive, but the design record's card rule names both block
            # types, so the orphan form gets its own card rather than being
            # dropped (preserve-and-flag).
            segments.append(f"> **[Tool: {block.name}]** {_result_summary(block)}")
        elif isinstance(block, DocumentBlock):
            # Copy D6: the attached document's export line (v1.3.1).
            segments.append(f"> **[Document: {document_title(block)}]**")
        else:
            segments.append(_unknown_segment(block))
        i += 1
    return segments


def _thinking_segment(block: ThinkingBlock) -> str:
    """``> **[Thinking]**`` label line — carrying the viewer's truncated /
    cut-off flags, in the reading view's order — then the thinking text as
    continued blockquote lines."""
    label = "> **[Thinking]**"
    if block.truncated:
        label += " · truncated"
    if block.cut_off:
        label += " · cut off"
    return "\n".join([label, *_blockquote_lines(block.thinking)])


def _tool_card(use: ToolUseBlock, result: ToolResultBlock | None) -> str:
    if use.name == "artifacts":
        # Placeholder card; the artifact body is 1.3+ extraction territory.
        # The paired result (a bare "OK" acknowledgement in every observed
        # call) is folded into the card like any other tool result.
        art_title = str(use.input.get("title") or "")
        art_type = str(use.input.get("type") or "")
        return f"> **[Artifact: {art_title} ({art_type})]** — content not exported."
    if result is None:
        return f"> **[Tool: {use.name}]** — no result recorded."
    return f"> **[Tool: {use.name}]** {_result_summary(result)}"


def _result_summary(result: ToolResultBlock) -> str:
    """The reading view's card meta, mirrored: inner-block count, and the
    same ``· error`` marker the reading view puts on an error result."""
    count = len(result.inner)
    summary = f"{count} inner block" + ("" if count == 1 else "s")
    if result.is_error:
        summary += " · error"
    return summary


def _attachment_segment(att: Attachment, include_text: bool) -> str:
    name = att.file_name or "(unnamed attachment)"
    if not include_text:
        return f"> **[Attachment: {name}]** — text not exported."
    return "\n".join([f"> **[Attachment: {name}]**", *_blockquote_lines(att.extracted_content)])


def _files_segment(files: list[FileRef]) -> str:
    """The one-line reference notice, mirroring the reading view's wording —
    the binaries are not in the archive, so only the names travel."""
    count = len(files)
    plural = "" if count == 1 else "s"
    names = ", ".join(f.file_name or "(unnamed)" for f in files)
    return f"> **[Files]** {count} binary file{plural} attached (not included in export): {names}"


def _branch_annotation(alternates: int) -> str:
    """Byte-exact per the Session N brief (both surfaces pluralised —
    the print label line in the detail template is the other one)."""
    if alternates == 1:
        return "> **[Branch point]** 1 alternate response exists and is not included in this file."
    return (
        f"> **[Branch point]** {alternates} alternate responses exist "
        "and are not included in this file."
    )


def document_title(block: DocumentBlock) -> str:
    """The document's display title, D6's fallback when the export has none."""
    return _single_line(block.title) if block.title else "Untitled document"


def _unknown_segment(block: object) -> str:
    """Preserve-and-flag for a block the serializer doesn't recognise.

    Live path since Session N: the parser preserves unknown raw block types
    as :class:`viewer.parser.UnknownBlock`, whose ``type`` attribute names
    the raw type here. Any other unrecognised object degrades to its class
    name rather than a crash or a silent drop.
    """
    type_name = getattr(block, "type", None) or type(block).__name__
    return f"> **[Unrecognised content block: {type_name}]**"


# ---------------------------------------------------------------------------
# Small text helpers.
# ---------------------------------------------------------------------------


def _blockquote_lines(text: str) -> list[str]:
    """Continued-blockquote form: every line prefixed ``> ``; blank lines
    become a bare ``>`` so the quote doesn't terminate mid-block."""
    return [f"> {line}" if line else ">" for line in text.splitlines()]


def _yaml_quote(value: str) -> str:
    """Double-quoted YAML scalar. Backslashes, quotes, and newlines are the
    only characters that can break the quoted form; all are valid escapes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _single_line(value: str) -> str:
    """Collapse any embedded newlines so a title stays one heading line."""
    return " ".join(value.splitlines())
