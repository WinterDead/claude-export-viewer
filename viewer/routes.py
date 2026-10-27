"""HTTP routes — the read-only views from plan §12 plus tagging from step 4.

Steps complete: 1 (home), 2 (conversation list), 3 (conversation detail with
branching switcher), 4 (project list/detail + manual conversation→project
tagging), 4.5 (bulk tagging), 5 (memory view, current step).

Step 4 ergonomic shape (confirmed in §12 step 4):
    - per-conversation tag dropdown on the conversation-detail page
    - bulk-tagging via ``/conversations?tag=untagged`` with inline dropdowns
    - project pages are read-only — no tagging UI lives there
"""

from __future__ import annotations

import dataclasses
import io
import logging
import os
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from viewer.cache import invalidate as invalidate_cache
from viewer.cache import set_fingerprint
from viewer.db import get_db
from viewer.export import (
    ExportOptions,
    bulk_zip_filename,
    export_filename,
    serialise_conversation,
)
from viewer.history import all_history, record_load
from viewer.inspect import begin_inspection, end_inspection, get_inspection
from viewer.parser import (
    CATEGORY_FRAMES,
    CONTAINER_LEGACY,
    KNOWN_CATEGORIES,
    LATER_CATEGORIES,
    MIXED_CONTAINER_MESSAGE,
    CategoryStructureError,
    Conversation,
    Export,
    ExportFormatError,
    MixedContainerError,
    Project,
    load_export,
    load_export_from_bytes_set,
    manifest_categories,
    manifest_file_count,
    manifest_links,
    manifest_part_map,
    sniff_container,
    sniff_manifest,
    validate_category_zip,
)
from viewer.render import (
    compute_render_path,
    conversation_display_title,
    slugify,
    split_frontmatter,
    split_help_page,
    split_memory_sections,
)
from viewer.search import SearchHit, index_export, search
from viewer.state import compute_tag_state, purge_orphan_tags
from viewer.tagging import (
    NO_PROJECT_LABEL,
    NO_PROJECT_SENTINEL,
    all_tags,
    get_tag,
    set_tag,
    tags_for_project,
)

bp = Blueprint("viewer", __name__)


# Sort modes (plan §12 steps 2 and 4).
CONV_SORT_OPTIONS: tuple[str, ...] = ("updated_desc", "created_desc", "title_asc")
DEFAULT_CONV_SORT = "updated_desc"

PROJECT_SORT_OPTIONS: tuple[str, ...] = ("updated_desc", "name_asc")
DEFAULT_PROJECT_SORT = "updated_desc"

MEMORY_SORT_OPTIONS: tuple[str, ...] = ("updated_desc", "name_asc", "created_desc")
DEFAULT_MEMORY_SORT = "updated_desc"

# Readable URL slug for the "No Project" filter (v1.1 Session B). The slug is
# the only public spelling — ``?tag=no-project`` maps to the sentinel
# internally and must stay stable now that it's chosen.
NO_PROJECT_SLUG = "no-project"

# Help-page copy (v1.1 Session C). A markdown file shipped with the app — the
# copy is editable without touching templates and flows through the same
# markdown → bleach pipeline as every other rendered blob.
HELP_CONTENT_PATH = Path(__file__).resolve().parent / "content" / "help.md"

# Theme (v1.3.0 Session T): default follows the system; the manual override
# persists in a first-party cookie so the server can render the theme class
# on <html> — no localStorage, no flash of the wrong theme. Disclosed in
# help ("Appearance").
THEMES: tuple[str, ...] = ("system", "light", "dark")
THEME_COOKIE = "viewer_theme"
THEME_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # a year is fine

# Hide legacy memory (v1.3.1, export-categories record §7): off by default;
# persisted the same way as the theme, disclosed in help ("Appearance").
LEGACY_MEMORY_COOKIE = "viewer_legacy_memory"
LEGACY_MEMORY_HIDE = "hide"

# Copy D1 and D2 (export-categories record appendix): the notices for files
# an Upload or Inspect submission skipped. Shown once, after the redirect.
SKIPPED_FILE_NOTICE = (
    "{filename} isn't a file this version of the viewer recognises, so it was skipped. "
    "The rest of your export loaded."
)
INVALID_CATEGORY_NOTICE = (
    "{filename} looks like a {category} file but couldn't be read, so it was skipped. "
    "The rest of your export loaded."
)
LOAD_NOTICE_CATEGORY = "load-notice"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sort_conversations(convs: list[Conversation], sort: str) -> list[Conversation]:
    if sort == "title_asc":
        return sorted(
            convs,
            key=lambda c: (conversation_display_title(c).lower(), c.uuid),
        )
    if sort == "created_desc":
        return sorted(convs, key=lambda c: (c.created_at, c.uuid), reverse=True)
    return sorted(convs, key=lambda c: (c.updated_at, c.uuid), reverse=True)


def _sort_projects(projects: list[Project], sort: str) -> list[Project]:
    if sort == "name_asc":
        return sorted(projects, key=lambda p: (p.name.lower(), p.uuid))
    return sorted(projects, key=lambda p: (p.updated_at, p.uuid), reverse=True)


def _filter_conversations_by_tag(
    convs: list[Conversation], tag: str | None, tags: dict[str, str]
) -> list[Conversation]:
    """Apply the ``?tag=`` filter on the conversation list.

    ``None`` → no filter. ``"untagged"`` → conversations with no row in
    ``conversation_project``. ``<project_uuid>`` (or the No-Project sentinel,
    already mapped from its URL slug by the route) → tagged to that value.
    Unknown values fall through to "no filter" (route-level fallback).
    """
    if tag is None:
        return convs
    if tag == "untagged":
        return [c for c in convs if c.uuid not in tags]
    return [c for c in convs if tags.get(c.uuid) == tag]


def _safe_redirect(next_url: str, fallback: str) -> str:
    """Return ``next_url`` if it's a same-site path; otherwise the fallback.

    Guards against open-redirect attempts via the ``next`` form field.
    """
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return fallback


def _current_export() -> Export:
    """The export the read-only views render: the inspected one while
    inspect mode is active (v1.2 Session K), otherwise the primary."""
    state = get_inspection(current_app)
    if state is not None:
        return state.export
    return current_app.config["EXPORT"]


def _refuse_while_inspecting() -> None:
    """Structural isolation for inspect mode: every mutating route calls
    this first and refuses outright, rather than trusting the templates to
    have hidden the controls."""
    if get_inspection(current_app) is not None:
        abort(403, "Read-only while inspecting an export. Return to your own export first.")


def artifact_part_opener():
    """The byte source for the viewed export's category ZIPs (export-categories
    record §6.1): inspect mode's in-memory parts, or the primary export's stored
    files. Returns a callable ``part -> path | file object | None`` for
    :func:`viewer.categories.read_member`; nothing is ever written."""
    state = get_inspection(current_app)
    if state is not None:
        blobs = state.part_blobs

        def from_memory(part: str):
            data = blobs.get(part)
            return io.BytesIO(data) if data is not None else None

        return from_memory

    export: Export = current_app.config["EXPORT"]
    active_set: Path = current_app.config["ACTIVE_SET_PATH"]

    def from_disk(part: str):
        for source in export.source_paths:
            path = Path(source)
            if path.name == part and path.is_file():
                return path
        stored = active_set / part
        return stored if stored.is_file() else None

    return from_disk


def legacy_memory_state(export: Export) -> bool:
    """True when the conversation-memory summary is **legacy** (record §7):
    the set contains one or more memory files. Triggered by the data, never
    by dates."""
    return bool(export.memory_files) and export.global_memory is not None


def legacy_memory_hidden(export: Export) -> bool:
    """Whether the legacy memory group and its search hits are hidden: the
    hide setting is on **and** the viewed set is in the legacy state. Inspect
    mode ignores the setting (record §7) — an inspected export never hides
    its summary."""
    if get_inspection(current_app) is not None:
        return False
    if request.cookies.get(LEGACY_MEMORY_COOKIE) != LEGACY_MEMORY_HIDE:
        return False
    return legacy_memory_state(export)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


@bp.route("/")
def index() -> str:
    export = _current_export()
    total_messages = sum(len(c.messages) for c in export.conversations)
    branched = sum(1 for c in export.conversations if c.branch_points)
    project_memories = sum(1 for p in export.projects if p.memory is not None)
    global_memory_block = (
        _memory_block(
            kind="global",
            anchor_id="global-memory",
            title="Conversation memory",
            body=export.global_memory.content,
        )
        if export.global_memory is not None and export.global_memory.content
        else None
    )
    return render_template(
        "index.html",
        total_conversations=len(export.conversations),
        total_messages=total_messages,
        branched_conversations=branched,
        total_projects=len(export.projects),
        project_memories=project_memories,
        orphan_memories=len(export.orphan_project_memories),
        global_memory_block=global_memory_block,
    )


# ---------------------------------------------------------------------------
# Conversations: list, detail, branching
# ---------------------------------------------------------------------------


@bp.route("/conversations")
def conversation_list() -> str:
    export = _current_export()
    sort = request.args.get("sort", DEFAULT_CONV_SORT)
    if sort not in CONV_SORT_OPTIONS:
        sort = DEFAULT_CONV_SORT

    if get_inspection(current_app) is not None:
        # Tags are primary-export state. In inspect mode the tagging UI and
        # the tag filter are structurally absent (v1.2 Session K), so no tag
        # state is computed or applied at all.
        tag_filter = None
        tags: dict[str, str] = {}
        orphan_project_tags: dict[str, str] = {}
        live_tags: dict[str, str] = {}
    else:
        tag_filter = request.args.get("tag") or None
        state = compute_tag_state(get_db(), export)
        # ``tags`` for the per-row dropdown: live tags + orphan-project tags so
        # the dropdown can render the orphan affordance. Orphan-conv tags don't
        # apply here — those convs aren't in the list at all.
        tags = {**state.live, **state.orphan_project}
        orphan_project_tags = state.orphan_project
        live_tags = state.live
        valid_filter = (
            tag_filter is None
            or tag_filter == "untagged"
            or tag_filter == NO_PROJECT_SLUG
            or any(p.uuid == tag_filter for p in export.projects)
        )
        if not valid_filter:
            tag_filter = None

    # The slug is the URL spelling; the sentinel is what's stored. Templates
    # keep seeing the slug (links round-trip), the filter sees the sentinel.
    internal_filter = NO_PROJECT_SENTINEL if tag_filter == NO_PROJECT_SLUG else tag_filter

    # Filtering uses live tags only — orphan-project tags don't match a real
    # project so they shouldn't satisfy a "Tagged: <Project>" filter anyway.
    filtered = _filter_conversations_by_tag(export.conversations, internal_filter, live_tags)
    conversations = _sort_conversations(filtered, sort)

    projects_sorted = sorted(export.projects, key=lambda p: p.name.lower())

    return render_template(
        "conversations.html",
        conversations=conversations,
        sort=sort,
        tag_filter=tag_filter,
        tags=tags,
        orphan_project_tags=orphan_project_tags,
        projects=projects_sorted,
        total_conversations=len(export.conversations),
    )


@bp.route("/conversations/<uuid>")
def conversation_detail(uuid: str) -> str:
    export = _current_export()
    conv = next((c for c in export.conversations if c.uuid == uuid), None)
    if conv is None:
        abort(404)

    branch_overrides = request.args.getlist("branch")
    rendered_path, ignored = compute_render_path(conv, branch_overrides)
    msg_by_uuid = {m.uuid: m for m in conv.messages}
    rendered_messages = [msg_by_uuid[u] for u in rendered_path if u in msg_by_uuid]
    rendered_set = set(rendered_path)
    switchers = _build_switchers(conv, rendered_set, branch_overrides, msg_by_uuid)

    if get_inspection(current_app) is not None:
        # Tagging is primary-export state; the dropdown is absent in inspect
        # mode, so nothing is read from the tag table.
        current_tag = None
        current_tag_orphan = False
    else:
        db = get_db()
        current_tag = get_tag(db, uuid)
        project_uuids = {p.uuid for p in export.projects}
        # The No-Project sentinel always "exists" — never orphan-flag it.
        current_tag_orphan = (
            bool(current_tag)
            and current_tag != NO_PROJECT_SENTINEL
            and current_tag not in project_uuids
        )
    projects_sorted = sorted(export.projects, key=lambda p: p.name.lower())

    return render_template(
        "conversation_detail.html",
        conv=conv,
        rendered_messages=rendered_messages,
        rendered_count=len(rendered_messages),
        total_count=len(conv.messages),
        switchers=switchers,
        ignored_overrides=ignored,
        current_tag=current_tag,
        current_tag_orphan=current_tag_orphan,
        projects=projects_sorted,
    )


def _build_switchers(
    conv: Conversation,
    rendered_set: set[str],
    branch_overrides: list[str],
    msg_by_uuid: dict[str, object],
) -> dict[str, list[dict[str, object]]]:
    """For each branch point on the rendered path, build the sibling-switcher rows."""
    switchers: dict[str, list[dict[str, object]]] = {}
    for bp_uuid in conv.branch_points:
        if bp_uuid not in rendered_set:
            continue
        children = [c for c in conv.children_by_uuid.get(bp_uuid, []) if c in msg_by_uuid]
        if len(children) <= 1:
            continue
        child_set = set(children)
        keep_overrides = [o for o in branch_overrides if o not in child_set]
        rows: list[dict[str, object]] = []
        for child in children:
            rows.append(
                {
                    "msg": msg_by_uuid[child],
                    "is_active": child in rendered_set,
                    "branch_args": [*keep_overrides, child],
                }
            )
        switchers[bp_uuid] = rows
    return switchers


# ---------------------------------------------------------------------------
# Conversation export (v1.2 Session M)
# ---------------------------------------------------------------------------


# Query/form values accepted as "on" for the two export toggles. Anything
# else — including an explicit 0/false — is off; an absent or empty value
# takes the option's design-locked default.
_EXPORT_FLAG_TRUE = frozenset({"1", "true", "yes", "on"})


def _export_flag(raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _EXPORT_FLAG_TRUE


def _export_options(values) -> ExportOptions:
    """Read the two toggles from query args or form data. Defaults are
    design-locked: thinking off, attachment text on."""
    return ExportOptions(
        include_thinking=_export_flag(values.get("thinking"), False),
        include_attachment_text=_export_flag(values.get("attachments"), True),
    )


def _export_project_label(conv_uuid: str) -> str | None:
    """Resolve the viewer-tag label for the ``project`` frontmatter field.

    ``None`` means the field is absent. Tags are primary-export state
    (v1.2 Session K), so while inspecting no tag is consulted at all —
    pairing YOUR tag rows with an inspected export's conversations is never
    meaningful. The No-Project sentinel exports as its display label
    (operator decision, Session N — the triage record is authored work and
    belongs in a full export); an orphan tag (project no longer in the
    archive) has no label to resolve, so it stays field-absent.
    """
    if get_inspection(current_app) is not None:
        return None
    tag = get_tag(get_db(), conv_uuid)
    if not tag:
        return None
    if tag == NO_PROJECT_SENTINEL:
        return NO_PROJECT_LABEL
    primary: Export = current_app.config["EXPORT"]
    project = next((p for p in primary.projects if p.uuid == tag), None)
    return project.name if project is not None else None


@bp.route("/conversations/<uuid>/export")
def conversation_export(uuid: str):
    """Download one conversation as a Markdown file.

    WYSIWYG per the design record: accepts the same ``branch`` parameters
    as the detail page and exports that displayed combination. Read-only by
    construction — the document is assembled in memory and returned as a
    browser download, so the route works while inspecting (a primary use
    case) and never writes server-side.

    ``frontmatter=0`` (Session N) drops the YAML header — the copy control
    fetches this form for the clipboard; the H1 title stays either way.
    """
    export = _current_export()
    conv = next((c for c in export.conversations if c.uuid == uuid), None)
    if conv is None:
        abort(404)

    path_uuids, _ignored = compute_render_path(conv, request.args.getlist("branch"))
    document = serialise_conversation(
        conv,
        path_uuids,
        _export_options(request.args),
        project_label=_export_project_label(uuid),
        exported_on=date.today(),
        include_frontmatter=_export_flag(request.args.get("frontmatter"), True),
    )
    response = Response(document, mimetype="text/markdown")
    response.headers["Content-Disposition"] = f'attachment; filename="{export_filename(conv)}"'
    return response


@bp.route("/conversations/export", methods=["POST"])
def conversation_export_bulk():
    """Download a selection of conversations as a zip of Markdown files.

    CSRF-protected like every other POST. Each conversation exports its
    default branch path; filename collisions inside the archive get a short
    uuid fragment (the *last* eight characters — the synthetic fixtures
    share their leading digits). The zip is assembled entirely in memory
    for the response, never written server-side, so this route too works
    while inspecting.
    """
    export = _current_export()
    conv_uuids = list(dict.fromkeys(u for u in request.form.getlist("conversation_uuid") if u))
    if not conv_uuids:
        abort(400, "no conversations selected")
    conv_by_uuid = {c.uuid: c for c in export.conversations}
    bad = next((u for u in conv_uuids if u not in conv_by_uuid), None)
    if bad is not None:
        abort(400, f"unknown conversation {bad}")

    options = _export_options(request.form)
    today = date.today()

    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for conv_uuid in conv_uuids:
            conv = conv_by_uuid[conv_uuid]
            name = export_filename(conv)
            if name in used_names:
                name = export_filename(conv, fragment=conv.uuid[-8:])
            if name in used_names:
                # Same slug, date, and fragment — the full uuid is unique.
                name = export_filename(conv, fragment=conv.uuid)
            used_names.add(name)
            document = serialise_conversation(
                conv,
                conv.canonical_path,
                options,
                project_label=_export_project_label(conv_uuid),
                exported_on=today,
            )
            zf.writestr(name, document)

    response = Response(buffer.getvalue(), mimetype="application/zip")
    response.headers["Content-Disposition"] = f'attachment; filename="{bulk_zip_filename(today)}"'
    return response


# ---------------------------------------------------------------------------
# Projects: list, detail
# ---------------------------------------------------------------------------


@bp.route("/projects")
def project_list() -> str:
    export = _current_export()
    sort = request.args.get("sort", DEFAULT_PROJECT_SORT)
    if sort not in PROJECT_SORT_OPTIONS:
        sort = DEFAULT_PROJECT_SORT
    projects = _sort_projects(export.projects, sort)

    if get_inspection(current_app) is not None:
        # Tag counts pair YOUR tags with YOUR projects — meaningless against
        # an inspected export's project list, so the template hides the stat
        # and no counts are computed.
        tag_counts: dict[str, int] = {}
    else:
        db = get_db()
        tag_counts = {p.uuid: len(tags_for_project(db, p.uuid)) for p in projects}

    return render_template(
        "projects.html",
        projects=projects,
        sort=sort,
        tag_counts=tag_counts,
    )


@bp.route("/projects/<uuid>")
def project_detail(uuid: str):
    export = _current_export()
    # The No-Project sentinel has no detail page — its "contents" are the
    # filtered conversations list, so send the request there instead of 404ing.
    if uuid == NO_PROJECT_SENTINEL:
        return redirect(url_for("viewer.conversation_list", tag=NO_PROJECT_SLUG))
    project = next((p for p in export.projects if p.uuid == uuid), None)
    if project is None:
        abort(404)

    if get_inspection(current_app) is not None:
        # Tagged-conversation lists mix YOUR tag rows with the inspected
        # export's conversations — never meaningful, so the section is
        # hidden by the template and nothing is read from the tag table.
        tagged_sorted: list[Conversation] = []
    else:
        db = get_db()
        tagged_uuids = set(tags_for_project(db, uuid))
        tagged = [c for c in export.conversations if c.uuid in tagged_uuids]
        tagged_sorted = _sort_conversations(tagged, DEFAULT_CONV_SORT)

    return render_template(
        "project_detail.html",
        project=project,
        tagged_conversations=tagged_sorted,
    )


# ---------------------------------------------------------------------------
# Memory view (plan §12 step 5)
# ---------------------------------------------------------------------------


@bp.route("/memory")
def memory_view() -> str:
    """The Memory page: whichever of the record §7 groups the archive
    supplies — conversation memory, memory files (the new memory system,
    current format only), and project memories (unchanged legacy behaviour,
    dangling-project preservation included).

    Memory files are grouped by whatever top-level path segments exist — an
    open set; the viewer never enumerates an expected taxonomy. Each memory
    is a top-level ``<details>`` (collapsed by default); per-section
    ``<details>`` (expanded by default) split conversation/project memories
    when headings are detectable.
    """
    export = _current_export()
    sort = request.args.get("sort", DEFAULT_MEMORY_SORT)
    if sort not in MEMORY_SORT_OPTIONS:
        sort = DEFAULT_MEMORY_SORT

    conversation_memory: dict | None = None
    if export.global_memory is not None and export.global_memory.content:
        conversation_memory = _memory_block(
            kind="global",
            anchor_id="global-memory",
            title="Conversation memory",
            body=export.global_memory.content,
        )

    # Memory files, grouped by first path segment ("" = files at the root),
    # groups and files ordered by path. Frontmatter split per record §7.
    file_groups: dict[str, list[dict]] = {}
    for mf in sorted(export.memory_files, key=lambda f: f.path):
        segments = mf.path.strip("/").split("/")
        group = segments[0] if len(segments) > 1 else ""
        fence, body = split_frontmatter(mf.content)
        file_groups.setdefault(group, []).append(
            {
                "path": mf.path,
                "anchor_id": f"memory-file-{slugify(mf.path)}",
                "updated_at": mf.updated_at,
                "frontmatter": fence,
                "body": body,
            }
        )
    # Root files first, then directory groups alphabetically.
    memory_file_groups = [
        {"name": name, "files": file_groups[name]}
        for name in sorted(file_groups, key=lambda n: (n != "", n))
    ]

    memories: list[dict] = []
    projects_with_memory = [p for p in export.projects if p.memory is not None]
    projects_with_memory = _sort_memories(projects_with_memory, sort)
    for p in projects_with_memory:
        memories.append(
            _memory_block(
                kind="project",
                anchor_id=f"memory-{p.uuid}",
                title=p.name,
                body=p.memory.content,
                project_uuid=p.uuid,
                is_starter=p.is_starter,
            )
        )

    orphans: list[dict] = []
    for mem in export.orphan_project_memories:
        orphans.append(
            _memory_block(
                kind="orphan",
                anchor_id=f"memory-{mem.project_uuid}",
                title=mem.project_uuid,  # surfaced in template as <code>
                body=mem.content,
            )
        )

    legacy = legacy_memory_state(export)
    hidden = legacy_memory_hidden(export)
    return render_template(
        "memory.html",
        conversation_memory=None if hidden else conversation_memory,
        memory_file_groups=memory_file_groups,
        memory_file_count=len(export.memory_files),
        memories=memories,
        orphans=orphans,
        sort=sort,
        legacy_memory=legacy,
        legacy_memory_hidden=hidden,
        # The control renders only in the legacy state of the primary
        # export — never while inspecting (record §7).
        show_legacy_control=legacy and get_inspection(current_app) is None,
    )


@bp.route("/memory/legacy", methods=["POST"])
def legacy_memory_set():
    """The Memory page's ``Hide legacy memory`` control (v1.3.1, record §7).
    Stores the choice in the ``viewer_legacy_memory`` cookie and returns to
    the Memory page. Display preference only: the export, the index, and the
    tags are untouched, so it is not refused while inspecting — but the
    control never renders there and inspect mode ignores the cookie."""
    hide = request.form.get("hide_legacy_memory") == "1"
    resp = redirect(url_for("viewer.memory_view"))
    resp.set_cookie(
        LEGACY_MEMORY_COOKIE,
        LEGACY_MEMORY_HIDE if hide else "show",
        max_age=THEME_COOKIE_MAX_AGE,
        samesite="Lax",
        httponly=True,
    )
    return resp


def _memory_block(
    *,
    kind: str,
    anchor_id: str,
    title: str,
    body: str,
    project_uuid: str | None = None,
    is_starter: bool = False,
) -> dict:
    """Build the dict the memory template iterates over.

    ``sections`` is ``[(title, content), ...]`` from
    :func:`viewer.render.split_memory_sections`. ``has_sections`` is True
    when at least one detected section has a non-empty title — used by the
    template to gate the per-memory expand/collapse-all toggle (no point
    showing it on a single-block fallback).
    """
    sections = split_memory_sections(body)
    has_sections = any(t for t, _ in sections)
    return {
        "kind": kind,
        "anchor_id": anchor_id,
        "title": title,
        "project_uuid": project_uuid,
        "is_starter": is_starter,
        "sections": sections,
        "has_sections": has_sections,
        "char_count": len(body or ""),
    }


def _sort_memories(projects: list[Project], sort: str) -> list[Project]:
    if sort == "name_asc":
        return sorted(projects, key=lambda p: (p.name.lower(), p.uuid))
    if sort == "created_desc":
        return sorted(projects, key=lambda p: (p.created_at, p.uuid), reverse=True)
    # updated_desc default
    return sorted(projects, key=lambda p: (p.updated_at, p.uuid), reverse=True)


# ---------------------------------------------------------------------------
# Account page (v1.3.0, record §8)
# ---------------------------------------------------------------------------


def _login_location(event) -> str:
    """City, region, country condensed — nulls omitted, ``—`` when empty."""
    parts = [p for p in (event.city, event.region, event.country) if p]
    return ", ".join(parts) if parts else "—"


def _login_browser_os(event) -> str:
    """The ``Browser & OS`` cell, condensed from the user-agent fields."""
    browser = " ".join(p for p in (event.browser_family, event.browser_version) if p)
    os_part = " ".join(p for p in (event.os_family, event.os_version) if p)
    parts = [p for p in (browser, os_part) if p]
    return " · ".join(parts) if parts else "—"


@bp.route("/account")
def account_view() -> str:
    """Identity fields from ``users.json`` plus, in the current format, the
    login history — newest first. Present whenever ``users.json`` is in the
    active set (both formats); 404 otherwise, matching the hidden nav entry.
    """
    export = _current_export()
    if export.user is None:
        abort(404)

    events = sorted(export.login_events, key=lambda e: e.timestamp, reverse=True)
    rows = [
        {
            "timestamp": e.timestamp,
            "method": e.method or "—",
            "ip_address": e.ip_address or "—",
            "location": _login_location(e),
            "browser_os": _login_browser_os(e),
        }
        for e in events
    ]
    return render_template("account.html", user=export.user, login_rows=rows)


# ---------------------------------------------------------------------------
# Search (plan §12 step 6)
# ---------------------------------------------------------------------------


SEARCH_RESULT_LIMIT = 200


@bp.route("/search")
def search_view() -> str:
    """Single-field FTS5 search across conversations, messages, projects,
    docs, memory, published artifacts, and scheduled tasks. Hits are grouped
    by their owning entity so a conversation with multiple matching messages
    renders as one card.

    In inspect mode the query runs against the inspected export's in-memory
    index — the primary FTS index in ``cache/viewer.sqlite`` is never read
    or written while inspecting."""
    export = _current_export()
    inspect_state = get_inspection(current_app)
    conn = inspect_state.search_conn if inspect_state is not None else get_db()
    query = (request.args.get("q") or "").strip()
    results = search(conn, query, limit=SEARCH_RESULT_LIMIT)

    conv_groups, project_groups, memory_global, memory_files, memory_orphans = _group_search_hits(
        results.hits, export
    )
    # Record §7: in the legacy state the summary's hit carries the D5 label,
    # and the hide setting removes it (display only — the index and the
    # result count are unchanged).
    if legacy_memory_hidden(export) and memory_global is not None:
        memory_global = None
        # Keep the shown count honest: the hidden hit leaves the total too.
        results = dataclasses.replace(results, total=max(results.total - 1, 0))

    return render_template(
        "search.html",
        query=query,
        results=results,
        conv_groups=conv_groups,
        project_groups=project_groups,
        memory_global=memory_global,
        memory_files=memory_files,
        memory_orphans=memory_orphans,
        result_limit=SEARCH_RESULT_LIMIT,
        legacy_memory=legacy_memory_state(export),
        artifact_hits=[h for h in results.hits if h.kind == "artifact"],
        task_hits=[h for h in results.hits if h.kind == "scheduled_task"],
    )


def _group_search_hits(
    hits: list[SearchHit], export: Export
) -> tuple[list[dict], list[dict], dict | None, list[dict], list[dict]]:
    """Bucket flat BM25-ordered hits into the structures the template needs.

    - Conversation/message hits group by ``conversation_uuid`` and carry a
      flag for whether each message is on the canonical path (used to decide
      between ``#msg-<uuid>`` and ``?branch=<uuid>#msg-<uuid>`` link forms).
    - Project meta/doc/memory hits group by ``project_uuid``. A project memory
      whose project doesn't resolve in the current archive becomes an orphan
      bucket — its link target is the orphan article in the memory view.
    - Global memory is a single optional hit.
    """
    conv_by_uuid = {c.uuid: c for c in export.conversations}
    project_by_uuid = {p.uuid: p for p in export.projects}

    conv_buckets: dict[str, dict] = {}
    project_buckets: dict[str, dict] = {}
    memory_global: dict | None = None
    memory_files: list[dict] = []
    memory_orphans: list[dict] = []

    for hit in hits:
        if hit.kind == "conversation" or hit.kind == "message":
            conv = conv_by_uuid.get(hit.conversation_uuid)
            if conv is None:
                continue  # ignore — index referenced a conv that's no longer in Export
            bucket = conv_buckets.setdefault(
                conv.uuid,
                {"conv": conv, "summary_hit": None, "message_hits": [], "best_rank": hit.rank},
            )
            bucket["best_rank"] = min(bucket["best_rank"], hit.rank)
            if hit.kind == "conversation":
                bucket["summary_hit"] = hit
            else:
                msg = next((m for m in conv.messages if m.uuid == hit.message_uuid), None)
                if msg is None:
                    continue
                bucket["message_hits"].append(
                    {
                        "msg": msg,
                        "hit": hit,
                        "is_canonical": msg.uuid in conv.canonical_path,
                    }
                )
        elif hit.kind in ("project_meta", "project_doc", "memory_project"):
            project = project_by_uuid.get(hit.project_uuid)
            if hit.kind == "memory_project" and project is None:
                # Orphan project memory — render in its own bucket.
                memory_orphans.append({"hit": hit})
                continue
            if project is None:
                continue
            bucket = project_buckets.setdefault(
                project.uuid,
                {
                    "project": project,
                    "meta_hit": None,
                    "memory_hit": None,
                    "doc_hits": [],
                    "best_rank": hit.rank,
                },
            )
            bucket["best_rank"] = min(bucket["best_rank"], hit.rank)
            if hit.kind == "project_meta":
                bucket["meta_hit"] = hit
            elif hit.kind == "memory_project":
                bucket["memory_hit"] = hit
            else:  # project_doc
                doc = next((d for d in project.docs if d.uuid == hit.doc_uuid), None)
                if doc is None:
                    continue
                bucket["doc_hits"].append({"doc": doc, "hit": hit})
        elif hit.kind == "memory_global":
            if memory_global is None or hit.rank < memory_global["hit"].rank:
                memory_global = {"hit": hit}
        elif hit.kind == "memory_file":
            # Keyed by path (the row's title); links to the file's anchor on
            # the Memory page (record §7).
            memory_files.append({"hit": hit, "anchor": f"memory-file-{slugify(hit.title)}"})

    conv_groups = sorted(conv_buckets.values(), key=lambda g: g["best_rank"])
    project_groups = sorted(project_buckets.values(), key=lambda g: g["best_rank"])
    memory_files.sort(key=lambda m: m["hit"].rank)
    return conv_groups, project_groups, memory_global, memory_files, memory_orphans


# ---------------------------------------------------------------------------
# Upload (plan §12 step 7; multi-file sets since v1.3.0, record §4/§5)
# ---------------------------------------------------------------------------


logger = logging.getLogger(__name__)

_MANIFEST_KIND = "manifest"


def _read_upload_cap_mb() -> int:
    """The per-file upload cap in MB — the documented configuration knob
    ``VIEWER_MAX_UPLOAD_MB``, read at startup, default 200 (record §4)."""
    raw = os.environ.get("VIEWER_MAX_UPLOAD_MB", "")
    if not raw:
        return 200
    try:
        mb = int(raw)
    except ValueError:
        logger.warning("VIEWER_MAX_UPLOAD_MB=%r is not a number — using the default 200", raw)
        return 200
    if mb <= 0:
        logger.warning("VIEWER_MAX_UPLOAD_MB=%r is not positive — using the default 200", raw)
        return 200
    return mb


# Per-file cap. The cap is a parse-memory guardrail, not a format limit: the
# current format's per-category delivery and Anthropic's own part-splitting
# make the default generous (the reference archive's largest file, the
# conversations zip, is 38 MB compressed), and power users with large
# archives can raise it locally via the knob without a release (record §4).
UPLOAD_MAX_BYTES = _read_upload_cap_mb() * 1024 * 1024

# Request-level backstop for Flask's MAX_CONTENT_LENGTH: one full set (four
# category zips plus the manifest) at the per-file cap. Pathological uploads
# beyond this are rejected before they stream into memory; the exact
# per-file rule is enforced while staging each file.
MAX_REQUEST_BYTES = UPLOAD_MAX_BYTES * 5


def _stream_capped(src, dest: Path, cap: int) -> bool:
    """Stream ``src`` to ``dest``; return False (dest removed) if it
    exceeds ``cap`` bytes."""
    written = 0
    with dest.open("wb") as out:
        while chunk := src.read(1 << 16):
            written += len(chunk)
            if written > cap:
                out.close()
                dest.unlink(missing_ok=True)
                return False
            out.write(chunk)
    return True


def _sniff_staged_file(path: Path, filename: str) -> str | None:
    """Identify a staged upload by content (record §2): a manifest ``.json``,
    a legacy archive, or a current category zip. ``None`` = unrecognised."""
    if filename.lower().endswith(".json"):
        return _MANIFEST_KIND if sniff_manifest(path.read_bytes()) else None
    try:
        with zipfile.ZipFile(path) as zf:
            return sniff_container(set(zf.namelist()))
    except zipfile.BadZipFile:
        return None


# Part suffix in current-format filenames (record §4): part order is
# metadata only the manifest and the filename carry.
_PART_SUFFIX_RE = re.compile(r"-(\d{3})\.zip$", re.IGNORECASE)


def _part_order(names: list[str], manifest_parts: dict[str, int]) -> list[int] | None:
    """Indices ordering same-category files as parts (record §4): by the
    manifest's part numbers when it assigns every name a distinct one,
    failing that by distinct ``-NNN`` filename suffixes. ``None`` when the
    files aren't distinguishable as parts (the last-wins guard applies)."""
    suffix_keys: list[int | None] = []
    for n in names:
        m = _PART_SUFFIX_RE.search(n)
        suffix_keys.append(int(m.group(1)) if m else None)
    for keys in ([manifest_parts.get(n) for n in names], suffix_keys):
        if all(k is not None for k in keys) and len(set(keys)) == len(keys):
            return sorted(range(len(names)), key=keys.__getitem__)
    return None


def summarise_set(slotted: dict[str, list[str]]) -> str:
    """The history/banner display name for a loaded set (record §5):
    ``kind → filenames``. A lone legacy archive keeps its filename —
    continuity with pre-set history rows; anything else summarises, with a
    part multiplier where a category was supplied as parts (record §4, copy
    C3), e.g. ``5 files: conversations ×2, projects, memories,
    light_metadata``."""
    if set(slotted) == {CONTAINER_LEGACY} and len(slotted[CONTAINER_LEGACY]) == 1:
        return slotted[CONTAINER_LEGACY][0]
    order = [*KNOWN_CATEGORIES, CONTAINER_LEGACY, _MANIFEST_KIND]
    parts: list[str] = []
    total = 0
    for k in order:
        names = slotted.get(k)
        if not names:
            continue
        total += len(names)
        parts.append(f"{k} ×{len(names)}" if len(names) > 1 else k)
    return f"{total} file{'s' if total != 1 else ''}: {', '.join(parts)}"


def summarise_set_dir(set_dir: Path) -> str:
    """:func:`summarise_set` over a stored set directory — the startup-load
    twin of the upload path's summary."""
    slotted: dict[str, list[str]] = {}
    for p in sorted(set_dir.iterdir()):
        if not p.is_file():
            continue
        kind = _sniff_staged_file(p, p.name)
        if kind is not None:
            slotted.setdefault(kind, []).append(p.name)
    return summarise_set(slotted) if slotted else set_dir.name


@bp.route("/upload", methods=["GET"])
def upload_form() -> str:
    return render_template("upload.html", error=None, cap_mb=UPLOAD_MAX_BYTES // (1024 * 1024))


def _upload_error(message: str, status: int = 400):
    return (
        render_template("upload.html", error=message, cap_mb=UPLOAD_MAX_BYTES // (1024 * 1024)),
        status,
    )


@bp.route("/upload", methods=["POST"])
def upload_post():
    _refuse_while_inspecting()
    files = [f for f in request.files.getlist("export_files") if f is not None and f.filename]
    if not files:
        return _upload_error("No file selected.")
    for f in files:
        if not f.filename.lower().endswith((".zip", ".json")):
            return _upload_error("Files must be .zip archives or a manifest .json.")

    active_set: Path = current_app.config["ACTIVE_SET_PATH"]
    active_zip: Path = current_app.config["ACTIVE_ZIP_PATH"]
    pickle_path: Path = current_app.config["PARSE_CACHE_PATH"]
    active_set.parent.mkdir(parents=True, exist_ok=True)

    # Stage the whole submission next to the active set, then sniff, parse,
    # and only on success swap it in — the parse-before-replace pattern
    # generalised to sets (record §4).
    stage = active_set.parent / "active_export.stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()

    try:
        slotted: dict[str, list[tuple[Path, str]]] = {}
        notices: list[str] = []
        unrecognised: list[str] = []
        unreadable: list[str] = []
        for f in files:
            basename = Path(f.filename).name
            dest = stage / basename
            try:
                if not _stream_capped(f.stream, dest, UPLOAD_MAX_BYTES):
                    return _upload_error(
                        f"{basename} exceeds the {UPLOAD_MAX_BYTES // (1024 * 1024)} MB "
                        "per-file cap."
                    )
            except OSError as e:
                return _upload_error(f"Could not save upload: {e}", 500)

            kind = _sniff_staged_file(dest, basename)
            if kind is None:
                # A6: skipped, never stored; the rest of the submission loads.
                dest.unlink(missing_ok=True)
                unrecognised.append(basename)
                notices.append(SKIPPED_FILE_NOTICE.format(filename=basename))
                continue
            if kind in LATER_CATEGORIES and not _staged_category_valid(dest, kind):
                dest.unlink(missing_ok=True)
                unreadable.append(basename)
                notices.append(INVALID_CATEGORY_NOTICE.format(filename=basename, category=kind))
                continue
            slotted.setdefault(kind, []).append((dest, basename))

        if not slotted and unrecognised:
            # No file in the submission is recognised: the existing error stands.
            return _upload_error(f"{unrecognised[0]} isn't a recognised export file.")

        # Same-category files: distinguishable parts merge in part order
        # through the loader's §3 multi-part merge; otherwise last file
        # wins, with a warning — the accidental double-pick guard (record §4).
        manifest_parts: dict[str, int] = {}
        if _MANIFEST_KIND in slotted:
            manifest_parts = manifest_part_map(slotted[_MANIFEST_KIND][-1][0].read_bytes())
        for kind, entries in slotted.items():
            if len(entries) < 2:
                continue
            order = (
                None
                if kind == _MANIFEST_KIND
                else _part_order([n for _p, n in entries], manifest_parts)
            )
            if order is not None:
                slotted[kind] = [entries[i] for i in order]
                continue
            *earlier, last = entries
            for prev_path, prev_name in earlier:
                logger.warning(
                    "duplicate %s file in one upload: %s replaces %s", kind, last[1], prev_name
                )
                if prev_path != last[0]:
                    prev_path.unlink(missing_ok=True)
            slotted[kind] = [last]

        zip_paths = [
            p for kind in (*KNOWN_CATEGORIES, CONTAINER_LEGACY) for p, _n in slotted.get(kind, [])
        ]
        if not zip_paths:
            if _MANIFEST_KIND not in slotted:
                # Only unreadable category files were supplied — nothing
                # loaded, so D2's "the rest loaded" would be untrue; the
                # existing parse-error form says what happened instead.
                return _upload_error(f"Couldn't parse archive: {unreadable[0]} couldn't be read.")
            return _upload_error(
                "The manifest alone doesn't contain your data — include the export "
                ".zip files as well."
            )

        # Validate by parsing BEFORE anything replaces the active set. A
        # malformed set surfaces as a friendly message and the previous
        # export stays in place.
        try:
            new_export = load_export(zip_paths)
        except MixedContainerError:
            return _upload_error(MIXED_CONTAINER_MESSAGE)
        except ExportFormatError as e:
            return _upload_error(f"Couldn't parse archive: {e}")

        # Fingerprint once, over the staged set as received (manifest
        # included) — the same digest keys the pickle cache and identifies
        # the set in the upload-history row.
        staged_files = sorted(p for p in stage.iterdir() if p.is_file())
        new_fingerprint = set_fingerprint(staged_files)

        # Commit: swap the staged set in, retire any pre-set active zip,
        # drop the stale parse cache, rebuild the index, swap the in-memory
        # Export last so a route in flight sees old or new fully.
        old = active_set.parent / "active_export.old"
        if old.exists():
            shutil.rmtree(old)
        if active_set.exists():
            active_set.rename(old)
        stage.rename(active_set)
        if old.exists():
            shutil.rmtree(old)
        active_zip.unlink(missing_ok=True)
    finally:
        if stage.exists():
            shutil.rmtree(stage)

    invalidate_cache(pickle_path)
    # The set was parsed from the staging directory; point its sources at the
    # stored files, so the footer is right and artifact bytes stay readable
    # after the stage is renamed into place (export-categories record §6.1).
    new_export = dataclasses.replace(
        new_export,
        source_paths=[str(active_set / Path(p).name) for p in new_export.source_paths],
    )

    db = get_db()
    index_export(db, new_export)
    current_app.config["EXPORT"] = new_export
    current_app.config["MANIFEST_STATUS"] = manifest_status_for_set(active_set)
    current_app.config["MANIFEST_CATEGORIES"] = manifest_categories_for_set(active_set)
    for notice in notices:
        flash(notice, LOAD_NOTICE_CATEGORY)
    # A successful upload replaced the stored set, so the graceful-startup
    # banner condition clears with it (v1.1.5 Session I).
    current_app.config["STORED_EXPORT_FAILED"] = False

    # Record after the swap succeeded. Uploads always record, even when the
    # fingerprint matches an earlier row — an explicit user action is worth
    # logging.
    record_load(
        db,
        source="upload",
        filename=summarise_set(
            {kind: [n for _p, n in entries] for kind, entries in slotted.items()}
        ),
        zip_sha256=new_fingerprint,
        export=new_export,
    )

    return redirect(url_for("viewer.index"))


def _staged_category_valid(path: Path, category: str) -> bool:
    """Structural validation of a staged later-category file (record §5.1)."""
    try:
        with zipfile.ZipFile(path) as zf:
            validate_category_zip(category, zf)
    except (CategoryStructureError, zipfile.BadZipFile):
        return False
    return True


def _bytes_category_valid(data: bytes, category: str) -> bool:
    """In-memory twin of :func:`_staged_category_valid` for Inspect."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            validate_category_zip(category, zf)
    except (CategoryStructureError, zipfile.BadZipFile):
        return False
    return True


def manifest_categories_for_set(set_dir: Path) -> tuple[str, ...]:
    """The stored manifest's distinct categories — the A7 banner's expected
    set — or ``()`` when the set carries no manifest."""
    if not set_dir.is_dir():
        return ()
    for p in sorted(set_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".json":
            found = manifest_categories(p.read_bytes())
            if found:
                return found
    return ()


def manifest_status_for_set(set_dir: Path) -> tuple[int, int] | None:
    """Expected-vs-supplied counts for the degraded-view banner (record §4):
    ``(manifest's data-file count, zips supplied)``, or ``None`` when the
    set carries no manifest. The manifest is metadata only — never fetched."""
    if not set_dir.is_dir():
        return None
    expected: int | None = None
    supplied = 0
    for p in sorted(set_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() == ".json":
            m = manifest_file_count(p.read_bytes())
            if m is not None:
                expected = m
        elif p.suffix.lower() == ".zip":
            supplied += 1
    if expected is None:
        return None
    return expected, supplied


# ---------------------------------------------------------------------------
# Inspect mode (v1.2 Session K; multi-file sets since v1.3.0)
# ---------------------------------------------------------------------------


@bp.route("/inspect", methods=["GET"])
def inspect_form() -> str:
    return render_template("inspect.html", error=None, cap_mb=UPLOAD_MAX_BYTES // (1024 * 1024))


def _inspect_error(message: str, status: int = 400):
    return (
        render_template("inspect.html", error=message, cap_mb=UPLOAD_MAX_BYTES // (1024 * 1024)),
        status,
    )


@bp.route("/inspect", methods=["POST"])
def inspect_post():
    """Open an export read-only, without touching the stored one.

    The no-trace rule: the uploaded files are read straight into memory and
    parsed from there — no tempfile step, no ``cache/`` write, no parse
    pickle, no history row. A parse failure errors on the Inspect page and
    leaves the currently viewed export (primary or previously inspected)
    untouched. Same multi-file picker and detection as Upload (record §4).
    """
    files = [f for f in request.files.getlist("export_files") if f is not None and f.filename]
    if not files:
        return _inspect_error("No file selected.")
    for f in files:
        if not f.filename.lower().endswith((".zip", ".json")):
            return _inspect_error("Files must be .zip archives or a manifest .json.")

    slotted: dict[str, list[tuple[str, bytes]]] = {}
    notices: list[str] = []
    unrecognised: list[str] = []
    unreadable: list[str] = []
    for f in files:
        basename = Path(f.filename).name
        data = f.stream.read(UPLOAD_MAX_BYTES + 1)
        if len(data) > UPLOAD_MAX_BYTES:
            return _inspect_error(
                f"{basename} exceeds the {UPLOAD_MAX_BYTES // (1024 * 1024)} MB per-file cap."
            )
        if basename.lower().endswith(".json"):
            kind: str | None = _MANIFEST_KIND if sniff_manifest(data) else None
        else:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    kind = sniff_container(set(zf.namelist()))
            except zipfile.BadZipFile:
                kind = None
        if kind is None:
            # A6, same rule as Upload: skipped with D1, the rest loads.
            unrecognised.append(basename)
            notices.append(SKIPPED_FILE_NOTICE.format(filename=basename))
            continue
        if kind in LATER_CATEGORIES and not _bytes_category_valid(data, kind):
            unreadable.append(basename)
            notices.append(INVALID_CATEGORY_NOTICE.format(filename=basename, category=kind))
            continue
        slotted.setdefault(kind, []).append((basename, data))

    if not slotted and unrecognised:
        return _inspect_error(f"{unrecognised[0]} isn't a recognised export file.")

    # Same rules as Upload (record §4): distinguishable parts keep their
    # part order for the loader's merge; otherwise last file wins.
    manifest_parts: dict[str, int] = {}
    if _MANIFEST_KIND in slotted:
        manifest_parts = manifest_part_map(slotted[_MANIFEST_KIND][-1][1])
    for kind, entries in slotted.items():
        if len(entries) < 2:
            continue
        order = (
            None
            if kind == _MANIFEST_KIND
            else _part_order([n for n, _d in entries], manifest_parts)
        )
        if order is not None:
            slotted[kind] = [entries[i] for i in order]
            continue
        *earlier, last = entries
        for prev_name, _d in earlier:
            logger.warning(
                "duplicate %s file in one inspection: %s replaces %s", kind, last[0], prev_name
            )
        slotted[kind] = [last]

    zips = [
        (name, data)
        for kind in (*KNOWN_CATEGORIES, CONTAINER_LEGACY)
        for name, data in slotted.get(kind, [])
    ]
    if not zips:
        if _MANIFEST_KIND not in slotted:
            return _inspect_error(f"Couldn't parse archive: {unreadable[0]} couldn't be read.")
        return _inspect_error(
            "The manifest alone doesn't contain your data — include the export "
            ".zip files as well."
        )

    try:
        export = load_export_from_bytes_set(zips)
    except MixedContainerError:
        return _inspect_error(MIXED_CONTAINER_MESSAGE)
    except ExportFormatError as e:
        return _inspect_error(f"Couldn't parse archive: {e}")

    manifest_status: tuple[int, int] | None = None
    expected_categories: tuple[str, ...] = ()
    if _MANIFEST_KIND in slotted:
        m = manifest_file_count(slotted[_MANIFEST_KIND][-1][1])
        if m is not None:
            manifest_status = (m, len(zips))
        expected_categories = manifest_categories(slotted[_MANIFEST_KIND][-1][1])

    display = summarise_set({kind: [n for n, _d in entries] for kind, entries in slotted.items()})
    begin_inspection(
        current_app,
        export,
        display,
        manifest_status=manifest_status,
        manifest_categories=expected_categories,
        part_blobs=dict(slotted.get(CATEGORY_FRAMES, [])),
    )
    for notice in notices:
        flash(notice, LOAD_NOTICE_CATEGORY)
    return redirect(url_for("viewer.index"))


# ---------------------------------------------------------------------------
# Manifest links page (v1.3.1, export-categories record §8, copy D7)
# ---------------------------------------------------------------------------

# Copy D7's error for anything that isn't a manifest.
MANIFEST_PAGE_ERROR = "That file isn't an export manifest."


def _https_url(url: str) -> bool:
    """Links render as anchors only for https URLs with a host (record §8)."""
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.netloc)


@bp.route("/manifest", methods=["GET"])
def manifest_links_form() -> str:
    return render_template("manifest.html", error=None, links=None)


@bp.route("/manifest", methods=["POST"])
def manifest_links_show() -> str:
    """Show a manifest's download links. The manifest is parsed in memory and
    never stored — no set write, no history row, no cache change (the inspect
    no-trace rule, applied); the browser follows a link only when the user
    clicks it, and the viewer never fetches one (amendment A8)."""
    f = request.files.get("manifest_file")
    if f is None or not f.filename:
        return render_template("manifest.html", error=MANIFEST_PAGE_ERROR, links=None), 400
    data = f.stream.read(UPLOAD_MAX_BYTES + 1)
    links = manifest_links(data) if len(data) <= UPLOAD_MAX_BYTES else None
    if links is None:
        return render_template("manifest.html", error=MANIFEST_PAGE_ERROR, links=None), 400
    rows = [
        {
            "filename": link.filename,
            "category": link.category,
            "url": link.url if _https_url(link.url) else None,
        }
        for link in links.files
    ]
    return render_template("manifest.html", error=None, links=links, rows=rows)


@bp.route("/theme", methods=["POST"])
def theme_set():
    """The nav Theme control (v1.3.0 Session T). Stores the choice in the
    ``viewer_theme`` cookie and bounces back to the submitting page. Not
    guarded by :func:`_refuse_while_inspecting`: the theme is app chrome,
    not export or tag state, so changing it mid-inspection is harmless."""
    theme = request.form.get("theme", "")
    if theme not in THEMES:
        abort(400, "Unknown theme.")
    resp = redirect(_safe_redirect(request.form.get("next", ""), url_for("viewer.index")))
    resp.set_cookie(
        THEME_COOKIE,
        theme,
        max_age=THEME_COOKIE_MAX_AGE,
        samesite="Lax",
        httponly=True,
    )
    return resp


@bp.route("/inspect/exit", methods=["POST"])
def inspect_exit():
    """The banner's "Return to my export" control. The primary export stayed
    resident throughout, so this just discards the inspect state. Harmless
    no-op when not inspecting."""
    end_inspection(current_app)
    return redirect(url_for("viewer.index"))


# ---------------------------------------------------------------------------
# Upload history (v1.1 Session A, item A3)
# ---------------------------------------------------------------------------


@bp.route("/history")
def history_view() -> str:
    """Reverse-chronological log of every export load — uploads and
    startup loads of a real archive. Doubles as an archive-growth view."""
    rows = all_history(get_db())
    return render_template("history.html", rows=rows)


# ---------------------------------------------------------------------------
# Help page (v1.1 Session C)
# ---------------------------------------------------------------------------


@bp.route("/help")
def help_view() -> str:
    """User guide rendered from ``viewer/content/help.md``.

    The file is read per request — it's small, and this keeps the copy
    editable without an app restart. A missing file is a true structural
    invariant (the file ships with the app), so the read is allowed to raise
    rather than degrade.
    """
    text = HELP_CONTENT_PATH.read_text(encoding="utf-8")
    title, intro, sections = split_help_page(text)
    return render_template("help.html", title=title, intro=intro, sections=sections)


# ---------------------------------------------------------------------------
# Orphan tag resolution (plan §12 step 7)
# ---------------------------------------------------------------------------


@bp.route("/orphans")
def orphans_view() -> str:
    """Inventory page for tag rows that no longer resolve against the
    current ``Export``. Two buckets: orphan-conv (the conversation is
    missing) and orphan-project (the project is missing). The page is the
    only purge surface — no other route auto-prunes.

    Deliberately reads the PRIMARY export even in inspect mode: tags are
    primary-export state, and resolving them against an inspected archive
    would misclassify every row. The purge form is hidden while inspecting
    (and the purge route refuses)."""
    export: Export = current_app.config["EXPORT"]
    state = compute_tag_state(get_db(), export)

    project_by_uuid = {p.uuid: p for p in export.projects}
    conv_by_uuid = {c.uuid: c for c in export.conversations}

    # Resolve what we can: orphan-conv rows have a project that may still
    # exist (show project name); orphan-project rows have a conv that
    # exists (show conv title). A sentinel row here is orphaned only on the
    # conversation dimension — the sentinel "project" exists by definition,
    # so it must never get the "project also missing" treatment.
    orphan_conv_rows = [
        {
            "conversation_uuid": cuid,
            "project_uuid": puid,
            "project": project_by_uuid.get(puid),
            "is_sentinel": puid == NO_PROJECT_SENTINEL,
        }
        for cuid, puid in sorted(state.orphan_conv.items())
    ]
    orphan_project_rows = [
        {
            "conversation_uuid": cuid,
            "project_uuid": puid,
            "conv": conv_by_uuid.get(cuid),
        }
        for cuid, puid in sorted(state.orphan_project.items())
    ]

    return render_template(
        "orphans.html",
        state=state,
        orphan_conv_rows=orphan_conv_rows,
        orphan_project_rows=orphan_project_rows,
    )


@bp.route("/orphans/purge", methods=["POST"])
def orphans_purge():
    """Delete every orphan tag row. Gated by an explicit ``confirm=yes``
    field in the form so a stray POST can't wipe state — the orphans
    template's checkbox emits the field only when the user has ticked it."""
    _refuse_while_inspecting()
    if request.form.get("confirm") != "yes":
        abort(400, "purge requires explicit confirmation")
    export: Export = current_app.config["EXPORT"]
    db = get_db()
    state = compute_tag_state(db, export)
    purge_orphan_tags(db, state)
    return redirect(url_for("viewer.orphans_view"))


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------


@bp.route("/tag", methods=["POST"])
def tag_conversation():
    _refuse_while_inspecting()
    export: Export = current_app.config["EXPORT"]
    conv_uuid = request.form.get("conversation_uuid", "").strip()
    project_uuid = request.form.get("project_uuid", "").strip() or None
    next_url = request.form.get("next", "")

    if not conv_uuid:
        abort(400, "missing conversation_uuid")
    if not any(c.uuid == conv_uuid for c in export.conversations):
        abort(404, f"unknown conversation {conv_uuid}")
    # Valid targets: a project in the current export, or the No-Project
    # sentinel — the one non-project value the dropdowns offer.
    if (
        project_uuid is not None
        and project_uuid != NO_PROJECT_SENTINEL
        and not any(p.uuid == project_uuid for p in export.projects)
    ):
        abort(400, f"unknown project {project_uuid}")

    set_tag(get_db(), conv_uuid, project_uuid)

    fallback = url_for("viewer.conversation_detail", uuid=conv_uuid)
    return redirect(_safe_redirect(next_url, fallback))


@bp.route("/tag/bulk", methods=["POST"])
def tag_bulk():
    """Bulk-tag, retag, or untag a set of conversations (plan §12 step 4.5)."""
    _refuse_while_inspecting()
    export: Export = current_app.config["EXPORT"]
    mode = request.form.get("mode", "")
    if mode not in ("tag", "retag", "untag"):
        abort(400, "invalid mode")

    conv_uuids = [u for u in request.form.getlist("conversation_uuid") if u]
    next_url = request.form.get("next", "")
    fallback = url_for("viewer.conversation_list")

    # Empty selection is a no-op redirect — keeps the action bar's "submit
    # without checking anything" path harmless.
    if not conv_uuids:
        return redirect(_safe_redirect(next_url, fallback))

    valid_conv = {c.uuid for c in export.conversations}
    bad = next((u for u in conv_uuids if u not in valid_conv), None)
    if bad is not None:
        abort(400, f"unknown conversation {bad}")

    db = get_db()

    if mode == "untag":
        for u in conv_uuids:
            set_tag(db, u, None)
    else:
        project_uuid = request.form.get("project_uuid", "").strip()
        if not project_uuid:
            abort(400, "missing project_uuid")
        # Same target rule as the single-tag route: export projects plus the
        # No-Project sentinel.
        if project_uuid != NO_PROJECT_SENTINEL and not any(
            p.uuid == project_uuid for p in export.projects
        ):
            abort(404, f"unknown project {project_uuid}")
        if mode == "tag":
            # Skip rows that already have a tag — friendlier on initial sweep.
            current = all_tags(db)
            for u in conv_uuids:
                if u not in current:
                    set_tag(db, u, project_uuid)
        else:  # retag
            for u in conv_uuids:
                set_tag(db, u, project_uuid)

    return redirect(_safe_redirect(next_url, fallback))
