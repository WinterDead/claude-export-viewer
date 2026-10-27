"""The later export categories (v1.4.0): published artifacts, artifact data, and
Claude Code scheduled tasks.

Models and loaders for ``frames``, ``artifact_data``, and ``claude_code_cloud``
(``docs/export-categories-design.md`` §6; source shapes in ``docs/export-schema.md``).
:mod:`viewer.parser` detects and validates the category ZIPs and calls the loaders here,
so everything still normalises into the one ``Export`` model.

**Bytes stay out of the model** (record §6.1). An artifact keeps names, metadata, and
per-file integrity results; file bytes are read on demand from the stored set (or the
in-memory set while inspecting) through :func:`read_member`. The one exception is
``search_text`` — the visible text and Markdown the search index needs — which never
includes scripts, styles, fonts, or bundles, so the parse cache grows with prose, not with
artifact payloads.

Preserve and flag throughout (house rule): an active version missing from the list, a
version without files, files present but unlisted, size or hash mismatches, duplicate
artifact ids across parts, unknown kinds and visibilities, unreadable comment files, data
for absent artifacts, and runs for absent routines are all kept and logged, never fatal.
Every flag is a short machine string on the record so the pages can surface it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

KNOWN_ARTIFACT_KINDS = frozenset({"artifact", "page"})
KNOWN_VISIBILITIES = frozenset({"private"})

LAYOUT_SINGLE_HTML = "single_html"
LAYOUT_MULTI_FILE = "multi_file"
LAYOUT_NONE = "none"

_ARTIFACT_MEMBER_RE = re.compile(r"artifacts/([^/]+)/(.+)")
_DATA_MEMBER_RE = re.compile(r"artifacts/([^/]+)/data/([^/]+)\.json")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _ts(value: Any) -> datetime | None:
    """A UTC-aware datetime, or ``None`` for a missing or malformed value."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("could not parse timestamp %r — kept as unknown", value)
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Models.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """One file of a multi-file version, with its integrity result (record §6.1)."""

    path: str  # ``exported_as`` — the only lookup key the preview route accepts
    original_path: str
    mime_type: str
    size_bytes: int | None
    sha256: str
    present: bool
    size_match: bool
    hash_match: bool

    @property
    def ok(self) -> bool:
        return self.present and self.size_match and self.hash_match


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    id: str
    title: str
    description: str
    created_at: datetime | None
    layout: str  # LAYOUT_SINGLE_HTML | LAYOUT_MULTI_FILE | LAYOUT_NONE
    files: list[ArtifactFile] = field(default_factory=list)
    # Files present in the version directory but absent from its file list —
    # preserved and flagged, never served (record §6.3, outcome 3).
    unlisted_files: list[str] = field(default_factory=list)
    # The entry page's ``exported_as`` for a multi-file version.
    entry_path: str | None = None
    flags: list[str] = field(default_factory=list)

    def file(self, path: str) -> ArtifactFile | None:
        """The listed file whose ``exported_as`` equals ``path`` exactly, or ``None``.
        Request paths are matched against the list, never joined to a filesystem path."""
        for f in self.files:
            if f.path == path:
                return f
        return None


@dataclass(frozen=True, slots=True)
class CommentReply:
    body: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class CommentThread:
    resolved: bool
    tab: str
    quoted_text: str
    comments: list[CommentReply]


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    kind: str  # raw; unknown values preserved and flagged
    visibility: str  # raw
    owner_account: str
    updated_at: datetime | None
    active_version: str | None
    # Display order: ``created_at``, newest first (record §6.1). The list order in
    # ``artifact.json`` carries no meaning (V7).
    versions: list[ArtifactVersion]
    # The category ZIP this artifact came from (its basename) — the byte-access key.
    part: str
    # ``page`` kind: the root Markdown member, and the comment threads (``None`` when
    # there is no comments file).
    content_member: str | None = None
    comments: list[CommentThread] | None = None
    # Searchable text: description plus visible entry-page text (scripts and styles
    # excluded) or the page Markdown, plus multi-file Markdown — never payload bytes.
    search_text: str = ""
    flags: list[str] = field(default_factory=list)

    @property
    def active(self) -> ArtifactVersion | None:
        """The active version, falling back to the newest when it is missing."""
        for v in self.versions:
            if v.id == self.active_version:
                return v
        return self.versions[0] if self.versions else None

    @property
    def title(self) -> str:
        version = self.active
        return version.title if version is not None else ""

    @property
    def created_at(self) -> datetime | None:
        """The earliest version's ``created_at`` — the "Recently created" sort key."""
        stamps = [v.created_at for v in self.versions if v.created_at is not None]
        return min(stamps) if stamps else None

    def version(self, version_id: str) -> ArtifactVersion | None:
        for v in self.versions:
            if v.id == version_id:
                return v
        return None


@dataclass(frozen=True, slots=True)
class ArtifactDataDocument:
    collection: str
    id: str
    created_at: datetime | None
    updated_at: datetime | None
    # Open set of user-defined keys — never interpreted (record §6.4).
    data: Any


@dataclass(frozen=True, slots=True)
class ArtifactDataFile:
    artifact_id: str
    name: str
    documents: list[ArtifactDataDocument]


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    routine_id: str
    name: str
    prompt: str
    prompt_withheld: bool
    enabled: bool
    cron: str | None
    run_once_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskRun:
    routine_id: str
    scheduled_for: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    status: str
    session_id: str


# ---------------------------------------------------------------------------
# Visible text for the search index (stdlib parser only, record §6.6).
# ---------------------------------------------------------------------------


class _VisibleText(HTMLParser):
    _SKIP = frozenset({"script", "style", "template", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def visible_text(html_bytes: bytes) -> str:
    """The text a reader would see in ``html_bytes``: script and style excluded."""
    parser = _VisibleText()
    try:
        parser.feed(html_bytes.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:  # noqa: BLE001 - HTMLParser is lenient; never fail a load on it
        logger.warning("could not extract visible text from an artifact page")
    return " ".join(parser.parts)


def html_has_script(html_bytes: bytes) -> bool:
    """True when the page carries a ``<script>`` element (record §6.3, outcome 5)."""
    return re.search(rb"<script\b", html_bytes, re.IGNORECASE) is not None


def html_has_external_resource(html_bytes: bytes) -> bool:
    """True when the page references an ``http(s)`` (or protocol-relative) resource."""
    text = html_bytes.decode("utf-8", errors="replace")
    attrs = re.search(
        r"""(?:src|href|srcset|poster|data)\s*=\s*["']?\s*(?:https?:)?//""", text, re.I
    )
    css = re.search(r"""(?:url\(\s*["']?|@import\s+["'])\s*(?:https?:)?//""", text, re.I)
    return attrs is not None or css is not None


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


def _read(zf: zipfile.ZipFile, member: str) -> bytes | None:
    try:
        return zf.read(member)
    except (KeyError, zipfile.BadZipFile, OSError):
        return None


def load_frames(zf: zipfile.ZipFile, part: str) -> list[Artifact]:
    """Every artifact in one ``frames`` ZIP, in directory order."""
    by_artifact: dict[str, dict[str, str]] = {}
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        m = _ARTIFACT_MEMBER_RE.fullmatch(name)
        if m:
            by_artifact.setdefault(m.group(1), {})[m.group(2)] = name
    return [_load_artifact(zf, part, aid, members) for aid, members in sorted(by_artifact.items())]


def _load_artifact(zf: zipfile.ZipFile, part: str, aid: str, members: dict[str, str]) -> Artifact:
    flags: list[str] = []
    descriptor: dict[str, Any] = {}
    raw = _read(zf, members["artifact.json"]) if "artifact.json" in members else None
    if raw is None:
        flags.append("no_descriptor")
        logger.warning("artifact %s has no artifact.json — preserved without metadata", aid)
    else:
        try:
            parsed = json.loads(raw)
            descriptor = parsed if isinstance(parsed, dict) else {}
        except (ValueError, UnicodeDecodeError):
            flags.append("no_descriptor")
            logger.warning("artifact %s has an unreadable artifact.json", aid)

    declared_id = _str(descriptor.get("id"))
    if declared_id and declared_id != aid:
        flags.append("id_mismatch")
        logger.warning("artifact %s declares id %s — keyed by its directory", aid, declared_id)
    kind = _str(descriptor.get("kind"))
    if descriptor and kind not in KNOWN_ARTIFACT_KINDS:
        flags.append("unknown_kind")
        logger.warning("artifact %s has unknown kind %r — preserved raw", aid, kind)
    visibility = _str(descriptor.get("visibility"))
    if descriptor and visibility not in KNOWN_VISIBILITIES:
        flags.append("unknown_visibility")
        logger.warning("artifact %s has unknown visibility %r — preserved raw", aid, visibility)

    accounted = {"artifact.json"}
    versions: list[ArtifactVersion] = []
    for entry in descriptor.get("versions") or []:
        if not isinstance(entry, dict) or not _str(entry.get("id")):
            flags.append("malformed_version")
            logger.warning("artifact %s lists a malformed version — skipped", aid)
            continue
        version, used = _load_version(zf, aid, members, entry, page=(kind == "page"))
        accounted |= used
        versions.append(version)

    # Version files the list doesn't name: preserved as unlisted on no version,
    # flagged on the artifact.
    stray = [rest for rest in members if rest.startswith("versions/") and rest not in accounted]
    if stray:
        flags.append("unattributed_version_files")
        logger.warning("artifact %s has %d version files no listed version claims", aid, len(stray))

    epoch = datetime.min.replace(tzinfo=UTC)
    versions.sort(key=lambda v: (v.created_at or epoch, v.id), reverse=True)
    active = _str(descriptor.get("active_version")) or None
    if active is not None and all(v.id != active for v in versions):
        flags.append("active_version_missing")
        logger.warning("artifact %s: active version is not in its version list", aid)
    if descriptor and not versions:
        flags.append("no_versions")

    content_member, comments, search_bits = (
        None,
        None,
        [v.description for v in versions if v.id == active],
    )
    root_files = sorted(rest for rest in members if "/" not in rest and rest != "artifact.json")
    if kind == "page":
        # Content decides, filenames never do: the root Markdown is the content and
        # the root JSON the comments, whatever they are named (V10).
        markdown = [r for r in root_files if r.lower().endswith(".md")]
        threads = [r for r in root_files if r.lower().endswith(".json")]
        if markdown:
            content_member = members[markdown[0]]
            body = _read(zf, content_member)
            if body is not None:
                search_bits.append(body.decode("utf-8", errors="replace"))
        if threads:
            comments = _load_comments(zf, members[threads[0]], aid, flags)
    else:
        version = next((v for v in versions if v.id == active), versions[0] if versions else None)
        if version is not None:
            search_bits.extend(_version_search_text(zf, aid, version))

    return Artifact(
        id=aid,
        kind=kind,
        visibility=visibility,
        owner_account=_str(descriptor.get("owner_account")),
        updated_at=_ts(descriptor.get("updated_at")),
        active_version=active,
        versions=versions,
        part=part,
        content_member=content_member,
        comments=comments,
        search_text="\n".join(bit for bit in search_bits if bit),
        flags=flags,
    )


def version_member(aid: str, version: ArtifactVersion, path: str | None = None) -> str:
    """The ZIP member holding a version's single HTML, or one of its listed files."""
    if version.layout == LAYOUT_SINGLE_HTML:
        return f"artifacts/{aid}/versions/{version.id}.html"
    return f"artifacts/{aid}/versions/{version.id}/{path}"


def _load_version(
    zf: zipfile.ZipFile,
    aid: str,
    members: dict[str, str],
    entry: dict[str, Any],
    *,
    page: bool,
) -> tuple[ArtifactVersion, set[str]]:
    vid = _str(entry.get("id"))
    common = {
        "id": vid,
        "title": _str(entry.get("title")),
        "description": _str(entry.get("description")),
        "created_at": _ts(entry.get("created_at")),
    }
    single = f"versions/{vid}.html"
    listing = f"versions/{vid}.files.json"
    prefix = f"versions/{vid}/"
    directory = {rest[len(prefix) :]: rest for rest in members if rest.startswith(prefix)}

    if single in members:
        return ArtifactVersion(layout=LAYOUT_SINGLE_HTML, **common), {single}

    if listing not in members and not directory:
        if not page:  # page versions carry no files by design (V10)
            logger.warning("artifact %s version %s has no files in the export", aid, vid)
        return (
            ArtifactVersion(layout=LAYOUT_NONE, flags=[] if page else ["no_files"], **common),
            set(),
        )

    flags: list[str] = []
    entries: list[Any] = []
    raw = _read(zf, members[listing]) if listing in members else None
    if raw is None:
        flags.append("no_file_list")
        logger.warning("artifact %s version %s has files but no file list", aid, vid)
    else:
        try:
            parsed = json.loads(raw)
            entries = parsed.get("files") if isinstance(parsed, dict) else None
            if not isinstance(entries, list):
                raise ValueError("no files list")
        except (ValueError, UnicodeDecodeError):
            entries = []
            flags.append("unreadable_file_list")
            logger.warning("artifact %s version %s has an unreadable file list", aid, vid)

    files: list[ArtifactFile] = []
    entry_path: str | None = None
    for item in entries:
        if not isinstance(item, dict):
            continue
        exported = _str(item.get("exported_as"))
        if not exported:
            continue
        size = item.get("size_bytes")
        size = size if isinstance(size, int) and not isinstance(size, bool) else None
        sha = _str(item.get("sha256")).lower()
        data = _read(zf, f"artifacts/{aid}/{prefix}{exported}") if exported in directory else None
        present = data is not None
        size_match = present and size == len(data)
        hash_match = present and hashlib.sha256(data).hexdigest() == sha
        mime = _str(item.get("mime_type"))
        files.append(
            ArtifactFile(
                path=exported,
                original_path=_str(item.get("original_path")),
                mime_type=mime,
                size_bytes=size,
                sha256=sha,
                present=present,
                size_match=size_match,
                hash_match=hash_match,
            )
        )
        if not present:
            logger.warning("artifact %s version %s: a listed file is missing", aid, vid)
        elif not (size_match and hash_match):
            logger.warning("artifact %s version %s: a file doesn't match its record", aid, vid)
        if entry_path is None and mime == "text/html" and "/" not in exported:
            entry_path = exported

    listed = {f.path for f in files}
    unlisted = sorted(p for p in directory if p not in listed)
    if unlisted:
        flags.append("unlisted_files")
        logger.warning("artifact %s version %s: %d files are not listed", aid, vid, len(unlisted))
    if entry_path is None:
        flags.append("no_entry_page")
    used = {listing, *directory.values()}
    return (
        ArtifactVersion(
            layout=LAYOUT_MULTI_FILE,
            files=files,
            unlisted_files=unlisted,
            entry_path=entry_path,
            flags=flags,
            **common,
        ),
        used,
    )


def _version_search_text(zf: zipfile.ZipFile, aid: str, version: ArtifactVersion) -> list[str]:
    bits: list[str] = []
    if version.layout == LAYOUT_SINGLE_HTML:
        data = _read(zf, version_member(aid, version))
        if data is not None:
            bits.append(visible_text(data))
    elif version.layout == LAYOUT_MULTI_FILE:
        if version.entry_path is not None:
            data = _read(zf, version_member(aid, version, version.entry_path))
            if data is not None:
                bits.append(visible_text(data))
        for f in version.files:
            if f.mime_type == "text/markdown" and f.present:
                data = _read(zf, version_member(aid, version, f.path))
                if data is not None:
                    bits.append(data.decode("utf-8", errors="replace"))
    return bits


def _load_comments(
    zf: zipfile.ZipFile, member: str, aid: str, flags: list[str]
) -> list[CommentThread] | None:
    raw = _read(zf, member)
    try:
        parsed = json.loads(raw) if raw is not None else None
        if not isinstance(parsed, list):
            raise ValueError("not a list of threads")
    except (ValueError, UnicodeDecodeError):
        flags.append("unreadable_comments")
        logger.warning("artifact %s has an unreadable comments file", aid)
        return None
    threads: list[CommentThread] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        replies = [
            CommentReply(body=_str(c.get("body")), created_at=_ts(c.get("created_at")))
            for c in item.get("comments") or []
            if isinstance(c, dict)
        ]
        threads.append(
            CommentThread(
                resolved=bool(item.get("resolved")),
                tab=_str(item.get("tab")),
                quoted_text=_str(item.get("quoted_text")),
                comments=replies,
            )
        )
    return threads


# ---------------------------------------------------------------------------
# artifact_data
# ---------------------------------------------------------------------------


def load_artifact_data(zf: zipfile.ZipFile) -> list[ArtifactDataFile]:
    out: list[ArtifactDataFile] = []
    for name in sorted(zf.namelist()):
        m = _DATA_MEMBER_RE.fullmatch(name)
        if not m:
            continue
        raw = _read(zf, name)
        try:
            parsed = json.loads(raw) if raw is not None else None
        except (ValueError, UnicodeDecodeError):
            parsed = None
        docs = parsed.get("documents") if isinstance(parsed, dict) else None
        if not isinstance(docs, list):
            logger.warning("artifact data file for %s is unreadable — skipped", m.group(1))
            continue
        out.append(
            ArtifactDataFile(
                artifact_id=m.group(1),
                name=m.group(2),
                documents=[
                    ArtifactDataDocument(
                        collection=_str(d.get("collection")),
                        id=_str(d.get("id")),
                        created_at=_ts(d.get("created_at")),
                        updated_at=_ts(d.get("updated_at")),
                        data=d.get("data"),
                    )
                    for d in docs
                    if isinstance(d, dict)
                ],
            )
        )
    return out


# ---------------------------------------------------------------------------
# claude_code_cloud
# ---------------------------------------------------------------------------


def load_claude_code_cloud(zf: zipfile.ZipFile) -> tuple[list[ScheduledTask], list[TaskRun]]:
    tasks: list[ScheduledTask] = []
    runs: list[TaskRun] = []
    for name in sorted(zf.namelist()):
        if not name.endswith(".json"):
            continue
        raw = _read(zf, name)
        try:
            parsed = json.loads(raw) if raw is not None else None
        except (ValueError, UnicodeDecodeError):
            parsed = None
        records = parsed.get("claude_code_cloud") if isinstance(parsed, dict) else None
        if not isinstance(records, list):
            continue
        leaf = PurePosixPath(name).name
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if leaf == "routines.json" or "schedule" in rec:
                schedule = rec.get("schedule") if isinstance(rec.get("schedule"), dict) else {}
                tasks.append(
                    ScheduledTask(
                        routine_id=_str(rec.get("routine_id")),
                        name=_str(rec.get("name")),
                        prompt=_str(rec.get("prompt")),
                        prompt_withheld=bool(rec.get("prompt_withheld")),
                        enabled=bool(rec.get("enabled")),
                        cron=(
                            schedule.get("cron") if isinstance(schedule.get("cron"), str) else None
                        ),
                        run_once_at=_ts(schedule.get("run_once_at")),
                        created_at=_ts(rec.get("created_at")),
                        updated_at=_ts(rec.get("updated_at")),
                        ended_at=_ts(rec.get("ended_at")),
                    )
                )
            else:
                runs.append(
                    TaskRun(
                        routine_id=_str(rec.get("routine_id")),
                        scheduled_for=_ts(rec.get("scheduled_for")),
                        started_at=_ts(rec.get("started_at")),
                        finished_at=_ts(rec.get("finished_at")),
                        status=_str(rec.get("status")),
                        session_id=_str(rec.get("session_id")),
                    )
                )
    return tasks, runs


def orphan_runs(tasks: list[ScheduledTask], runs: list[TaskRun]) -> list[TaskRun]:
    """Runs whose routine is not in the export — preserved under D9's orphan heading."""
    known = {t.routine_id for t in tasks}
    return [r for r in runs if r.routine_id not in known]


# ---------------------------------------------------------------------------
# Cross-references (record §6.5, provisional)
# ---------------------------------------------------------------------------


def artifact_ids_in(value: Any, known: set[str] | frozenset[str]) -> list[str]:
    """Known artifact ids named anywhere in a tool ``input`` or ``structured_content``
    value, in first-seen order. Prose in tool results is deliberately not searched."""
    if value is None or not known:
        return []
    found: list[str] = []
    for match in _UUID_RE.findall(json.dumps(value, ensure_ascii=False)):
        if match in known and match not in found:
            found.append(match)
    return found


# ---------------------------------------------------------------------------
# On-demand byte access (record §6.1, §6.3)
# ---------------------------------------------------------------------------


def read_member(opener, part: str, member: str) -> bytes | None:
    """Read one member of the category ZIP named ``part``.

    ``opener(part)`` returns a readable ZIP source (a path or a file-like object) or
    ``None``; the caller supplies it, so the stored set and inspect mode's in-memory
    set share this one reader. Returns ``None`` when anything is missing.
    """
    source = opener(part)
    if source is None:
        return None
    try:
        with zipfile.ZipFile(source) as zf:
            return zf.read(member)
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
