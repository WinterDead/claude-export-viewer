"""FTS5 full-text search over the parsed Export (plan §12 step 6).

Single virtual table with a ``kind`` column plus parent-id columns so result
grouping happens in SQL rather than via Python-side joins. Indexed columns
are ``title`` and ``body``; everything else is ``UNINDEXED``.

Indexed kinds:
    conversation    title=conv.title, body=conv.summary
    message         body=concat of TextBlock/ThinkingBlock + attachments
    project_meta    title=project.name, body=project.description
    project_doc     title=doc.filename, body=doc.content
    memory_global   body=global_memory.content (the conversation memory)
    memory_project  body=project_memory.content (resolved + orphan)
    memory_file     title=file path, body=file content (v1.3.0, record §7)

Snippets use ``\\x02hit\\x02 … \\x02/hit\\x02`` as start/end markers. The
substitution to ``<mark>`` happens in :func:`viewer.render.render_search_snippet`
*before* the snippet hits ``markdown_to_html``. python-markdown uses ``\\x02``
as its own internal placeholder marker and silently strips bare STX bytes from
input — substituting first means ``<mark>`` rides the standard markdown→bleach
path (``<mark>`` is on the allowlist) instead of being eaten en route.

Pre-existing ``\\x02`` bytes in source bodies are scrubbed at index time with
a logged warning (preserve-and-flag), so a hexdump paste cannot collide with
the sentinels.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from viewer.parser import Export, Message

logger = logging.getLogger(__name__)


# Sentinel pair used by ``snippet()`` for match start/end. Replaced by
# ``<mark>`` / ``</mark>`` in the render layer. Body text is scrubbed of any
# pre-existing ``\x02`` byte at index time so a paste containing one cannot
# collide with the sentinels.
SENTINEL_OPEN = "\x02hit\x02"
SENTINEL_CLOSE = "\x02/hit\x02"
_SENTINEL_ELLIPSIS = "…"
_SENTINEL_MAX_TOKENS = 32


# Single virtual FTS5 table. Schema is dropped + recreated on every
# :func:`index_export` call — full reindex is sub-second at the scale we run at
# (single-digit thousands of messages) and removes a class of incremental-sync
# bugs.
_SCHEMA = """
DROP TABLE IF EXISTS search;
CREATE VIRTUAL TABLE search USING fts5(
    kind UNINDEXED,
    conversation_uuid UNINDEXED,
    message_uuid UNINDEXED,
    parent_message_uuid UNINDEXED,
    project_uuid UNINDEXED,
    doc_uuid UNINDEXED,
    title,
    body,
    tokenize = "unicode61 remove_diacritics 1"
);
"""


# Column indexes used by snippet() — body is the field worth surfacing in
# nearly every kind, and it always exists.
_BODY_COL_IDX = 7


# ---------------------------------------------------------------------------
# Result records.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single match. ``snippet`` carries sentinel bytes; render with
    :func:`viewer.render.render_search_snippet` before display."""

    kind: str
    title: str
    snippet: str
    rank: float
    conversation_uuid: str = ""
    message_uuid: str = ""
    parent_message_uuid: str = ""
    project_uuid: str = ""
    doc_uuid: str = ""


@dataclass(frozen=True, slots=True)
class SearchResults:
    query: str
    hits: list[SearchHit]
    total: int
    capped: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Indexing.
# ---------------------------------------------------------------------------


def index_export(conn: sqlite3.Connection, export: Export) -> int:
    """Drop and rebuild the search index from an in-memory ``Export``.

    Runs in a single transaction so route handlers never observe a
    partially-populated index. Returns the row count after population. Raises
    :class:`RuntimeError` if the populated count doesn't match the expected
    count (a parser-side change that adds a new searchable surface without a
    corresponding update here).
    """
    expected = _expected_row_count(export)

    conn.executescript(_SCHEMA)

    with conn:
        for conv in export.conversations:
            conn.execute(
                """
                INSERT INTO search (kind, conversation_uuid, title, body)
                VALUES ('conversation', ?, ?, ?)
                """,
                (conv.uuid, _scrub(conv.title), _scrub(conv.summary)),
            )
            for msg in conv.messages:
                conn.execute(
                    """
                    INSERT INTO search (
                        kind, conversation_uuid, message_uuid,
                        parent_message_uuid, body
                    )
                    VALUES ('message', ?, ?, ?, ?)
                    """,
                    (
                        conv.uuid,
                        msg.uuid,
                        msg.parent_uuid or "",
                        _scrub(_extract_message_body(msg)),
                    ),
                )

        for project in export.projects:
            conn.execute(
                """
                INSERT INTO search (kind, project_uuid, title, body)
                VALUES ('project_meta', ?, ?, ?)
                """,
                (project.uuid, _scrub(project.name), _scrub(project.description)),
            )
            for doc in project.docs:
                conn.execute(
                    """
                    INSERT INTO search (kind, project_uuid, doc_uuid, title, body)
                    VALUES ('project_doc', ?, ?, ?, ?)
                    """,
                    (project.uuid, doc.uuid, _scrub(doc.filename), _scrub(doc.content)),
                )
            if project.memory is not None:
                conn.execute(
                    "INSERT INTO search (kind, project_uuid, body) "
                    "VALUES ('memory_project', ?, ?)",
                    (project.uuid, _scrub(project.memory.content)),
                )

        if export.global_memory is not None and export.global_memory.content:
            conn.execute(
                "INSERT INTO search (kind, body) VALUES ('memory_global', ?)",
                (_scrub(export.global_memory.content),),
            )

        # The new memory system's files (record §7): one row per file, keyed
        # by its path — the path is the title so hits read as the file.
        for mf in export.memory_files:
            conn.execute(
                "INSERT INTO search (kind, title, body) VALUES ('memory_file', ?, ?)",
                (_scrub(mf.path), _scrub(mf.content)),
            )

        for orphan in export.orphan_project_memories:
            conn.execute(
                "INSERT INTO search (kind, project_uuid, body) " "VALUES ('memory_project', ?, ?)",
                (orphan.project_uuid, _scrub(orphan.content)),
            )

    actual = conn.execute("SELECT COUNT(*) FROM search").fetchone()[0]
    if actual != expected:
        raise RuntimeError(
            f"search index population mismatch: expected {expected} rows, got {actual}"
        )
    return actual


def _expected_row_count(export: Export) -> int:
    convs = len(export.conversations)
    msgs = sum(len(c.messages) for c in export.conversations)
    projs = len(export.projects)
    docs = sum(len(p.docs) for p in export.projects)
    proj_mems = sum(1 for p in export.projects if p.memory is not None)
    orphans = len(export.orphan_project_memories)
    global_mem = 1 if export.global_memory is not None and export.global_memory.content else 0
    memory_files = len(export.memory_files)
    return convs + msgs + projs + docs + proj_mems + orphans + global_mem + memory_files


def _extract_message_body(msg: Message) -> str:
    """Concatenate the text-bearing content of a message into one searchable blob.

    Includes ``TextBlock.text`` and ``ThinkingBlock.thinking`` (the prose
    surfaces a user is likely to search for) plus ``Attachment.extracted_content``
    (pasted file bodies). Tool blocks are intentionally skipped — their inputs
    and outputs are mostly structured noise and would dominate the index.
    """
    from viewer.parser import TextBlock, ThinkingBlock

    parts: list[str] = []
    for block in msg.content:
        if isinstance(block, TextBlock) and block.text:
            parts.append(block.text)
        elif isinstance(block, ThinkingBlock) and block.thinking:
            parts.append(block.thinking)
    for att in msg.attachments:
        if att.extracted_content:
            parts.append(att.extracted_content)
    return "\n\n".join(parts)


def _scrub(text: str | None) -> str:
    """Strip ``\\x02`` bytes from a body so the snippet sentinels remain unique.

    Per CLAUDE.md preserve-and-flag: log a warning rather than raising. A
    single bad paste shouldn't block the whole index. Returns ``""`` for
    falsy input so ``INSERT`` calls don't have to guard.
    """
    if not text:
        return ""
    if "\x02" in text:
        logger.warning(
            "search index: stripped %d STX byte(s) from a body before indexing",
            text.count("\x02"),
        )
        text = text.replace("\x02", "")
    return text


# ---------------------------------------------------------------------------
# Query preprocessing.
# ---------------------------------------------------------------------------


# Recognises the four bare boolean/proximity operators FTS5 honours
# (case-sensitive, must be standalone whitespace-delimited tokens). ``NEAR``
# may carry a parenthesised argument list — we still detect it as an operator
# because the open-paren on its own would already trip the char check below.
_OPERATOR_TOKEN_RE = re.compile(r"^(AND|OR|NOT|NEAR(?:\([^)]*\))?)$")


def _has_explicit_operators(query: str) -> bool:
    """Return ``True`` if the user appears to be writing FTS5 syntax.

    Triggers on:

    - Any of ``"``, ``*``, ``(``, ``)`` anywhere in the string. These
      characters don't show up in normal prose, so their presence is a clear
      "I meant FTS5 syntax" signal.
    - A leading ``+`` on a whitespace-delimited token (e.g. ``foo +bar``,
      the FTS5 phrase-adjacency operator). Hyphens inside or leading tokens
      (``TR-808``, ``covid-19``, ``-bar``) are deliberately *not* treated as
      operators: leading-``-`` is far more common in natural-language
      product names and identifiers than as a deliberate exclusion attempt,
      and FTS5 doesn't actually support bare ``-`` exclusion anyway. Users
      who want exclusion get the discoverable ``NOT`` path.
    - A bare uppercase ``AND`` / ``OR`` / ``NOT`` / ``NEAR`` token. FTS5 only
      treats these as operators when uppercase, so lowercase ``and`` in
      ``fish and chips`` is correctly classified as natural language.
    """
    if any(c in query for c in '"*()'):
        return True
    for tok in query.split():
        if len(tok) > 1 and tok[0] == "+":
            return True
        if _OPERATOR_TOKEN_RE.match(tok):
            return True
    return False


def preprocess_query(query: str) -> str:
    """Adapt a natural-language query to one FTS5 will accept.

    FTS5's parser raises ``OperationalError`` on bare punctuation (commas,
    periods, apostrophes, hyphens with leading whitespace, etc.) when used
    outside a quoted phrase. So if no explicit operator syntax is detected,
    each whitespace-delimited token is wrapped in double quotes — turning
    every token into a phrase whose contents are tokenised by ``unicode61``
    on insert/match, matching how the indexed text was tokenised.

    Examples (natural-language → FTS5)::

        "Engineering, a B.Sc."   →   '"Engineering," "a" "B.Sc."'
        "don't worry"            →   '"don\\'t" "worry"'
        "v1.2.3 release"         →   '"v1.2.3" "release"'

    Operator syntax passes through unchanged::

        '"exact phrase"'         →   '"exact phrase"'
        "foo*"                   →   "foo*"
        "foo AND bar"            →   "foo AND bar"
        "foo -bar"               →   "foo -bar"

    Whitespace-only and empty inputs return the empty string, which the
    caller (:func:`search`) short-circuits before ever hitting FTS5.
    """
    stripped = query.strip()
    if not stripped:
        return ""
    if _has_explicit_operators(stripped):
        return stripped
    return " ".join(f'"{tok}"' for tok in stripped.split())


# ---------------------------------------------------------------------------
# Querying.
# ---------------------------------------------------------------------------


def search(conn: sqlite3.Connection, query: str, *, limit: int = 200) -> SearchResults:
    """Run a full-text search and return up to ``limit`` hits, BM25-ordered.

    Accepts either natural-language input (preprocessed via
    :func:`preprocess_query` so bare punctuation doesn't crash FTS5) or
    explicit FTS5 syntax (``"quoted"``, ``foo*``, ``NEAR``, boolean
    operators) — operator syntax is detected and passed through unchanged.

    Empty queries return an empty result set without error. Truly malformed
    operator queries (unclosed quote, stray paren) are caught at the SQLite
    layer and surfaced via ``SearchResults.error``.
    """
    if not query.strip():
        return SearchResults(query=query, hits=[], total=0, capped=False)

    fts_query = preprocess_query(query)

    sql = f"""
        SELECT kind, conversation_uuid, message_uuid, parent_message_uuid,
               project_uuid, doc_uuid, title,
               snippet(search, {_BODY_COL_IDX}, ?, ?, ?, ?) AS snippet,
               bm25(search) AS rank
          FROM search
         WHERE search MATCH ?
         ORDER BY rank
         LIMIT ?
    """
    count_sql = "SELECT COUNT(*) FROM search WHERE search MATCH ?"

    try:
        total = conn.execute(count_sql, (fts_query,)).fetchone()[0]
        rows = conn.execute(
            sql,
            (
                SENTINEL_OPEN,
                SENTINEL_CLOSE,
                _SENTINEL_ELLIPSIS,
                _SENTINEL_MAX_TOKENS,
                fts_query,
                limit,
            ),
        ).fetchall()
    except sqlite3.OperationalError as e:
        return SearchResults(
            query=query,
            hits=[],
            total=0,
            capped=False,
            error=f"Query syntax error: {e}",
        )

    hits = [
        SearchHit(
            kind=r[0],
            conversation_uuid=r[1] or "",
            message_uuid=r[2] or "",
            parent_message_uuid=r[3] or "",
            project_uuid=r[4] or "",
            doc_uuid=r[5] or "",
            title=r[6] or "",
            snippet=r[7] or "",
            rank=r[8],
        )
        for r in rows
    ]
    return SearchResults(query=query, hits=hits, total=total, capped=total > limit)
