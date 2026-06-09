"""Shared rendering helpers — markdown-to-HTML and timestamp formatting.

These are surfaced as Jinja filters by ``app.create_app`` and are also imported
directly by tests. Keep this module side-effect free and dependency-light: no
Flask imports, so the helpers can be unit-tested without booting an app.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import bleach
import markdown as _markdown
from markupsafe import Markup

if TYPE_CHECKING:
    from viewer.parser import ContentBlock, Conversation

# Extensions enabled on every markdown render. ``fenced_code`` parses
# triple-backtick blocks (essential for Claude output), ``tables`` covers the
# pipe-table syntax, ``sane_lists`` stops loose-list edge cases, ``nl2br``
# converts single newlines into <br> so prose-style messages don't collapse.
_MARKDOWN_EXTENSIONS = ("fenced_code", "tables", "sane_lists", "nl2br")

# Allowlist of tags the sanitiser will keep. Everything not on this list is
# stripped (``strip=True`` below), keeping inner text but dropping the tag.
# Covers the full output surface of python-markdown with the extensions above,
# nothing more.
_ALLOWED_TAGS = frozenset(
    {
        # Block
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "hr",
        "br",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        # Inline
        "strong",
        "em",
        "del",
        "ins",
        "a",
        "img",
        "sup",
        "sub",
        "kbd",
        # ``<mark>`` is LOAD-BEARING — do not remove on the grounds that
        # search highlighting is "added after bleach". The original plan was
        # bleach-then-substitute; the actual implementation flipped to
        # substitute-then-markdown_to_html (because python-markdown silently
        # strips ``\x02`` placeholders, see ``render_search_snippet``), which
        # means the ``<mark>`` tags now flow *through* bleach inside
        # ``markdown_to_html`` and only survive because of this allowlist
        # entry. Removing it breaks search highlighting end-to-end.
        "mark",
    }
)
_SUMMARY_PREVIEW_MAX_CHARS = 200

_ALLOWED_ATTRS: dict[str, list[str]] = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "code": ["class"],  # language-* for fenced code blocks
    "pre": ["class"],
    "th": ["align"],
    "td": ["align"],
}
# bleach's default protocol set is ["http", "https", "mailto"]; ``javascript:``
# and ``data:`` URLs are excluded, so a markdown link with a javascript: href
# is rendered with the href dropped (the visible link text remains).


def markdown_to_html(text: str | None) -> Markup:
    """Render markdown to sanitised HTML, returning empty ``Markup`` for empty input.

    Used uniformly for every markdown-bearing field — ``TextBlock.text``,
    ``GlobalMemory.content``, ``ProjectMemory.content``, ``ProjectDoc.content``.
    Single helper, single allowlist, so output stays consistent across the
    conversation, project, and memory views.

    The threat model is essentially nil for a localhost personal tool, but
    inputs are markdown blobs authored by Claude in past conversations and may
    contain raw HTML or javascript-scheme links. Sanitisation is one library
    call and removes the "essentially" from "essentially nil":

    1. python-markdown produces HTML (raw HTML in the source passes through).
    2. ``bleach.clean`` strips every tag and attribute not on the allowlists
       above, and drops disallowed URL schemes.
    3. The result is wrapped in ``Markup`` so Jinja doesn't re-escape it.
    """
    if not text:
        return Markup("")
    raw_html = _markdown.markdown(text, extensions=list(_MARKDOWN_EXTENSIONS))
    clean_html = bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
    )
    return Markup(clean_html)


def render_search_snippet(snippet_with_sentinels: str | None) -> Markup:
    """Render an FTS5 snippet (carrying ``\\x02hit\\x02 … \\x02/hit\\x02`` sentinels)
    as sanitised HTML with the matches wrapped in ``<mark>`` tags.

    Pipeline order: substitute sentinels to ``<mark>``/``</mark>`` **first**,
    then run the result through ``markdown_to_html`` (which bleaches with
    ``<mark>`` on the allowlist).

    Why this order, not the obvious "bleach then substitute": python-markdown
    uses ``\\x02`` as its own internal placeholder marker. Sentinels left in
    the input are silently stripped by ``markdown.markdown()`` before bleach
    ever sees them. Substituting *before* markdown sidesteps that — the
    ``<mark>`` tags then ride the standard markdown→bleach path, surviving
    because the allowlist accepts them.

    Consequence — and the reason this matters for cleanup passes: the
    ``"mark"`` entry in ``_ALLOWED_TAGS`` is **load-bearing** for this
    function, not defensive. The original design called for substitution to
    happen *after* bleach, which would have made the allowlist entry merely
    nice-to-have. The actual implementation flipped that order, so removing
    ``"mark"`` from the allowlist would now silently break every search
    highlight on the page. See the comment at the allowlist for the same
    warning from the other side.

    Importing :mod:`viewer.search` here keeps that module Flask-free and
    breaks what would otherwise be a circular import.
    """
    from viewer.search import SENTINEL_CLOSE, SENTINEL_OPEN

    if not snippet_with_sentinels:
        return Markup("")
    pre = snippet_with_sentinels.replace(SENTINEL_OPEN, "<mark>").replace(SENTINEL_CLOSE, "</mark>")
    return markdown_to_html(pre)


def summary_preview(text: str | None, max_chars: int = _SUMMARY_PREVIEW_MAX_CHARS) -> str:
    """Render a markdown blob as a short plain-text preview suitable for a list row.

    Strips every markdown tag via ``bleach.clean(..., tags=[])`` — the same
    sanitiser as :func:`markdown_to_html`, just configured to keep nothing.
    Collapses whitespace runs and truncates to ``max_chars`` with a trailing
    ellipsis when the input would overflow.

    Used for ``Conversation.summary`` in list views: at least one conversation
    in the reference export has a multi-paragraph essay summary with literal
    ``**markdown**`` markers; rendering it raw makes that one row dwarf the
    rest. ``Markup`` is intentionally **not** returned — the result is plain
    text and Jinja's auto-escape will handle it.
    """
    if not text:
        return ""
    raw_html = _markdown.markdown(text, extensions=list(_MARKDOWN_EXTENSIONS))
    plain = bleach.clean(raw_html, tags=[], attributes={}, strip=True)
    plain = " ".join(plain.split())
    if len(plain) > max_chars:
        plain = plain[:max_chars].rstrip() + "…"
    return plain


# Display labels for message senders (v1.1 Session A). Inputs aren't
# necessarily from a human — agentic pipelines and dual-terminal workflows
# also drive the "human" side — so the accurate label is "User". Display
# layer only: the parser's raw ``sender`` values, the FTS index, and the
# pickle cache are untouched.
_SENDER_LABELS = {"human": "User", "assistant": "Assistant"}


def sender_label(sender: str | None) -> str:
    """Map a raw parser ``sender`` value to its user-facing label.

    Unknown senders pass through unchanged (preserve-and-flag: the parser
    already logged a warning at parse time; the renderer surfaces the raw
    value rather than hiding it behind a wrong label).
    """
    if not sender:
        return ""
    return _SENDER_LABELS.get(sender, sender)


def format_local(ts: datetime | None) -> str:
    """Render a datetime in the system's local timezone.

    - **Aware** datetimes: converted via ``ts.astimezone()`` (no argument), which
      uses whatever ``tzlocal`` reports for the host. The display TZ is **not**
      hardcoded — it follows the machine the viewer runs on.
    - **Naïve** datetimes: treated as UTC, then converted. Everything from the
      parser is UTC-aware, but other callers may pass naïve values (e.g. server
      "now" for a "last refreshed" indicator, manual tag timestamps), and this
      filter must not crash on them.
    - ``None``: returns the em-dash placeholder ``"—"``.

    Format: ``YYYY-MM-DD HH:MM:SS TZN`` (e.g. ``2026-04-05 03:11:30 PDT``).
    """
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def format_local_relative(ts: datetime | None, *, now: datetime | None = None) -> str:
    """Render a datetime as a coarse relative string (e.g. ``3 days ago``).

    Same naïve-tolerance contract as :func:`format_local` — naïve values are
    treated as UTC. Future timestamps render as ``in N <unit>``. Returns the
    em-dash for ``None``. The ``now`` keyword is for testability.
    """
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    delta_seconds = (now - ts).total_seconds()
    future = delta_seconds < 0
    seconds = abs(delta_seconds)

    if seconds < 45:
        return "in a moment" if future else "just now"
    if seconds < 3600:
        n, unit = max(1, int(seconds // 60)), "minute"
    elif seconds < 86400:
        n, unit = int(seconds // 3600), "hour"
    elif seconds < 86400 * 30:
        n, unit = int(seconds // 86400), "day"
    elif seconds < 86400 * 365:
        n, unit = int(seconds // (86400 * 30)), "month"
    else:
        n, unit = int(seconds // (86400 * 365)), "year"

    plural = "" if n == 1 else "s"
    return f"in {n} {unit}{plural}" if future else f"{n} {unit}{plural} ago"


# ---------------------------------------------------------------------------
# Display-title fallback for conversations.
# ---------------------------------------------------------------------------


_TITLE_PREVIEW_MAX_CHARS = 80


def conversation_display_title(conv: Conversation) -> str:
    """Resolve a display title for a conversation, falling back when ``title`` is empty.

    Per plan §12 step 2, ~5% of real-export conversations have no ``name``
    field. The chain is:

    1. ``conv.title`` if it has any non-whitespace content.
    2. ``"Untitled conversation — <first ~80 chars of first human message>"``
       — synthesised preview from the first human message's text content.
    3. ``"Untitled conversation · YYYY-MM-DD"`` — date-only fallback when the
       conversation has no human messages or no text content.

    The function never returns an empty string.
    """
    # Local import keeps render.py free of a hard parser dependency at module
    # load time, so the helpers can be unit-tested without booting Flask.
    from viewer.parser import TextBlock

    if conv.title.strip():
        return conv.title

    for msg in conv.messages:
        if msg.sender != "human":
            continue
        parts = [b.text for b in msg.content if isinstance(b, TextBlock) and b.text]
        joined = " ".join(parts).strip()
        if joined:
            preview = joined[:_TITLE_PREVIEW_MAX_CHARS].rstrip()
            if len(joined) > _TITLE_PREVIEW_MAX_CHARS:
                preview = preview + "…"
            return f"Untitled conversation — {preview}"
        break  # first human message had no text — don't keep walking

    date_str = conv.created_at.astimezone().strftime("%Y-%m-%d")
    return f"Untitled conversation · {date_str}"


# ---------------------------------------------------------------------------
# Branch-aware path resolution for the conversation detail view.
# ---------------------------------------------------------------------------


def compute_render_path(
    conv: Conversation, branch_overrides: list[str]
) -> tuple[list[str], list[str]]:
    """Resolve which message-uuid chain to render given branch overrides.

    Walks down from the canonical root. At each step:

    1. If any uuid in ``branch_overrides`` matches one of the current node's
       children, that child wins (and is "consumed").
    2. Otherwise, if we are still on ``conv.canonical_path``, follow it.
    3. Otherwise (off-canonical with no override), pick the visited child with
       the latest ``created_at`` — the same heuristic the parser uses to pick
       the canonical leaf.

    Cycles cannot occur because the parser already excludes unreachable
    messages from ``canonical_path`` and from ``children_by_uuid``\\'s
    reachable set, but we keep a ``visited`` guard for safety.

    Returns ``(rendered_path_uuids, ignored_overrides)`` — the second list is
    the override uuids that didn't match any child encountered, surfaced so
    the route can warn rather than silently dropping them.
    """
    if not conv.canonical_path:
        return [], list(branch_overrides)

    msg_by_uuid = {m.uuid: m for m in conv.messages}
    overrides_set = set(branch_overrides)
    consumed: set[str] = set()

    path: list[str] = [conv.canonical_path[0]]
    visited: set[str] = set(path)

    while True:
        current = path[-1]
        children = conv.children_by_uuid.get(current, [])
        if not children:
            break

        chosen: str | None = None
        for child in children:
            if child in overrides_set:
                chosen = child
                consumed.add(child)
                break

        if chosen is None:
            # Still on canonical_path? Follow it.
            try:
                idx = conv.canonical_path.index(current)
            except ValueError:
                idx = -1
            if 0 <= idx < len(conv.canonical_path) - 1:
                nxt = conv.canonical_path[idx + 1]
                if nxt in children:
                    chosen = nxt

        if chosen is None:
            # Off-canonical, no override → leaf-most-by-time fallback.
            usable = [c for c in children if c in msg_by_uuid and c not in visited]
            if not usable:
                break
            chosen = max(usable, key=lambda c: msg_by_uuid[c].created_at)

        if chosen in visited:
            break  # cycle safety net
        visited.add(chosen)
        path.append(chosen)

    ignored = [o for o in branch_overrides if o not in consumed]
    return path, ignored


# ---------------------------------------------------------------------------
# Memory section detection — for the collapsible memory view (plan §12 polish).
# ---------------------------------------------------------------------------


_BOLD_HEADING_RE = re.compile(r"^\s*\*\*([^*]+?)\*\*\s*$")
_HASH_HEADING_RE = re.compile(r"^\s*(#{1,4})\s+(.+?)\s*$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def split_memory_sections(body: str | None) -> list[tuple[str, str]]:
    """Detect section headings in a memory body and split it into chunks.

    Recognised heading forms (each must occupy a line by itself):

    - **Standalone bold paragraph**: ``**Title**``. The dominant convention
      in real memories — Claude writes section markers as bold paragraphs.
    - **ATX heading**: ``#`` through ``####`` followed by the title.

    Returns a list of ``(title, content)`` tuples. Content preceding the
    first heading is returned with ``title=""`` (preamble) so callers can
    render it without a collapsible wrapper.

    If no headings are detected, returns ``[("", body)]`` — a single
    title-less chunk — so the caller can render the whole body as one block
    without forcing an empty collapsible. Blank/None input also returns
    ``[("", "")]`` rather than raising.

    Section detection is intentionally lenient: structural variation is
    expected because memory bodies are markdown Claude wrote across many
    sessions, not enforced templates.
    """
    if not body or not body.strip():
        return [("", body or "")]

    lines = body.splitlines()
    boundaries: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _BOLD_HEADING_RE.match(line)
        if m:
            boundaries.append((i, m.group(1).strip()))
            continue
        m = _HASH_HEADING_RE.match(line)
        if m:
            boundaries.append((i, m.group(2).strip()))

    if not boundaries:
        return [("", body)]

    sections: list[tuple[str, str]] = []
    # Preamble: content before the first heading.
    if boundaries[0][0] > 0:
        preamble = "\n".join(lines[: boundaries[0][0]]).strip()
        if preamble:
            sections.append(("", preamble))

    for idx, (b_idx, title) in enumerate(boundaries):
        next_b_idx = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        content = "\n".join(lines[b_idx + 1 : next_b_idx]).strip()
        sections.append((title, content))

    return sections


# ---------------------------------------------------------------------------
# Help-page structure detection (v1.1 Session C).
# ---------------------------------------------------------------------------


_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def split_help_page(text: str | None) -> tuple[str, str, list[dict]]:
    """Split the help-page markdown into ``(title, intro, sections)``.

    Structure contract (Session C brief, C3):

    - The first ``#`` heading is the page title.
    - Everything above the first ``##`` (minus the title line) is the intro,
      rendered uncollapsed at the top of the page.
    - Each ``##`` opens a top-level section; each ``###`` inside it opens a
      subsection. Content between a ``##`` and its first ``###`` is the
      section's ``preamble``.
    - ``####`` and deeper are *not* structural — they stay in the content and
      render as ordinary markdown headings.
    - Heading markers inside fenced code blocks are ignored.

    Each section dict carries ``title``, ``slug`` (via :func:`slugify`,
    de-duplicated with a numeric suffix so anchors stay unique), ``preamble``,
    and ``subsections`` — a list of ``{"title", "slug", "content"}`` dicts.

    Unlike :func:`split_memory_sections` this is *not* lenient about heading
    style: help.md is authored content with strict heading discipline, not
    Claude-written memory prose, so only ATX ``##``/``###`` are structural.
    """
    title = ""
    intro_lines: list[str] = []
    sections: list[dict] = []
    seen_slugs: set[str] = set()

    def unique_slug(heading: str) -> str:
        base = slugify(heading)
        slug, n = base, 2
        while slug in seen_slugs:
            slug = f"{base}-{n}"
            n += 1
        seen_slugs.add(slug)
        return slug

    current_section: dict | None = None
    current_sub: dict | None = None
    in_fence = False

    for line in (text or "").splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        if not in_fence:
            m = _H2_RE.match(line)
            if m:
                current_section = {
                    "title": m.group(1),
                    "slug": unique_slug(m.group(1)),
                    "preamble_lines": [],
                    "subsections": [],
                }
                current_sub = None
                sections.append(current_section)
                continue
            m = _H3_RE.match(line)
            if m and current_section is not None:
                current_sub = {
                    "title": m.group(1),
                    "slug": slugify(m.group(1)),
                    "content_lines": [],
                }
                current_section["subsections"].append(current_sub)
                continue
            m = _H1_RE.match(line)
            if m and current_section is None and not title:
                title = m.group(1)
                continue

        if current_sub is not None:
            current_sub["content_lines"].append(line)
        elif current_section is not None:
            current_section["preamble_lines"].append(line)
        else:
            intro_lines.append(line)

    for sec in sections:
        sec["preamble"] = "\n".join(sec.pop("preamble_lines")).strip()
        for sub in sec["subsections"]:
            sub["content"] = "\n".join(sub.pop("content_lines")).strip()

    return title, "\n".join(intro_lines).strip(), sections


def slugify(text: str | None) -> str:
    """Convert a section title to a fragment-id-safe slug.

    ``"Work context"`` → ``"work-context"``. Empty / falsy / non-alphanumeric
    input returns ``"section"`` so anchors always have a stable target.
    """
    if not text:
        return "section"
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s or "section"


def block_type(block: ContentBlock) -> str:
    """Return a stable string tag for a content block — used by templates.

    Avoids ``block.__class__.__name__`` peppered through the Jinja, and keeps
    the parser's class names from leaking into the template layer.
    """
    from viewer.parser import TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

    if isinstance(block, TextBlock):
        return "text"
    if isinstance(block, ThinkingBlock):
        return "thinking"
    if isinstance(block, ToolUseBlock):
        return "tool_use"
    if isinstance(block, ToolResultBlock):
        return "tool_result"
    return "unknown"
