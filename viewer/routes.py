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

import shutil
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from viewer.cache import invalidate as invalidate_cache
from viewer.cache import zip_hash
from viewer.db import get_db
from viewer.history import all_history, record_load
from viewer.parser import Conversation, Export, ExportFormatError, Project, load_export
from viewer.render import (
    compute_render_path,
    conversation_display_title,
    split_help_page,
    split_memory_sections,
)
from viewer.search import SearchHit, index_export, search
from viewer.state import compute_tag_state, purge_orphan_tags
from viewer.tagging import NO_PROJECT_SENTINEL, all_tags, get_tag, set_tag, tags_for_project

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


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


@bp.route("/")
def index() -> str:
    export: Export = current_app.config["EXPORT"]
    total_messages = sum(len(c.messages) for c in export.conversations)
    branched = sum(1 for c in export.conversations if c.branch_points)
    project_memories = sum(1 for p in export.projects if p.memory is not None)
    global_memory_block = (
        _memory_block(
            kind="global",
            anchor_id="global-memory",
            title="Global memory",
            body=export.global_memory.content,
        )
        if export.global_memory.content
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
    export: Export = current_app.config["EXPORT"]
    sort = request.args.get("sort", DEFAULT_CONV_SORT)
    if sort not in CONV_SORT_OPTIONS:
        sort = DEFAULT_CONV_SORT

    tag_filter = request.args.get("tag") or None
    state = compute_tag_state(get_db(), export)
    # ``tags`` for the per-row dropdown: live tags + orphan-project tags so
    # the dropdown can render the orphan affordance. Orphan-conv tags don't
    # apply here — those convs aren't in the list at all.
    tags = {**state.live, **state.orphan_project}
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
    filtered = _filter_conversations_by_tag(export.conversations, internal_filter, state.live)
    conversations = _sort_conversations(filtered, sort)

    projects_sorted = sorted(export.projects, key=lambda p: p.name.lower())

    return render_template(
        "conversations.html",
        conversations=conversations,
        sort=sort,
        tag_filter=tag_filter,
        tags=tags,
        orphan_project_tags=state.orphan_project,
        projects=projects_sorted,
        total_conversations=len(export.conversations),
    )


@bp.route("/conversations/<uuid>")
def conversation_detail(uuid: str) -> str:
    export: Export = current_app.config["EXPORT"]
    conv = next((c for c in export.conversations if c.uuid == uuid), None)
    if conv is None:
        abort(404)

    branch_overrides = request.args.getlist("branch")
    rendered_path, ignored = compute_render_path(conv, branch_overrides)
    msg_by_uuid = {m.uuid: m for m in conv.messages}
    rendered_messages = [msg_by_uuid[u] for u in rendered_path if u in msg_by_uuid]
    rendered_set = set(rendered_path)
    switchers = _build_switchers(conv, rendered_set, branch_overrides, msg_by_uuid)

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
# Projects: list, detail
# ---------------------------------------------------------------------------


@bp.route("/projects")
def project_list() -> str:
    export: Export = current_app.config["EXPORT"]
    sort = request.args.get("sort", DEFAULT_PROJECT_SORT)
    if sort not in PROJECT_SORT_OPTIONS:
        sort = DEFAULT_PROJECT_SORT
    projects = _sort_projects(export.projects, sort)

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
    export: Export = current_app.config["EXPORT"]
    # The No-Project sentinel has no detail page — its "contents" are the
    # filtered conversations list, so send the request there instead of 404ing.
    if uuid == NO_PROJECT_SENTINEL:
        return redirect(url_for("viewer.conversation_list", tag=NO_PROJECT_SLUG))
    project = next((p for p in export.projects if p.uuid == uuid), None)
    if project is None:
        abort(404)

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
    """Single page rendering every memory blob in the archive (plan §12 step 5,
    polished per the collapsibility brief).

    Layout: global memory always first, then project memories sorted per the
    ``?sort=`` query string, then orphan memories (project_uuid no longer in
    the archive — preserve-and-flag).

    Each memory is rendered as a top-level ``<details>`` (collapsed by
    default in the template); per-section ``<details>`` (expanded by
    default) split the body when section headings are detectable.
    """
    export: Export = current_app.config["EXPORT"]
    sort = request.args.get("sort", DEFAULT_MEMORY_SORT)
    if sort not in MEMORY_SORT_OPTIONS:
        sort = DEFAULT_MEMORY_SORT

    memories: list[dict] = []

    if export.global_memory.content:
        memories.append(
            _memory_block(
                kind="global",
                anchor_id="global-memory",
                title="Global memory",
                body=export.global_memory.content,
            )
        )

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

    return render_template(
        "memory.html",
        memories=memories,
        orphans=orphans,
        sort=sort,
    )


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
# Search (plan §12 step 6)
# ---------------------------------------------------------------------------


SEARCH_RESULT_LIMIT = 200


@bp.route("/search")
def search_view() -> str:
    """Single-field FTS5 search across conversations, messages, projects,
    docs, and memory. Hits are grouped by their owning entity so a
    conversation with multiple matching messages renders as one card."""
    export: Export = current_app.config["EXPORT"]
    query = (request.args.get("q") or "").strip()
    results = search(get_db(), query, limit=SEARCH_RESULT_LIMIT)

    conv_groups, project_groups, memory_global, memory_orphans = _group_search_hits(
        results.hits, export
    )

    return render_template(
        "search.html",
        query=query,
        results=results,
        conv_groups=conv_groups,
        project_groups=project_groups,
        memory_global=memory_global,
        memory_orphans=memory_orphans,
        result_limit=SEARCH_RESULT_LIMIT,
    )


def _group_search_hits(
    hits: list[SearchHit], export: Export
) -> tuple[list[dict], list[dict], dict | None, list[dict]]:
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

    conv_groups = sorted(conv_buckets.values(), key=lambda g: g["best_rank"])
    project_groups = sorted(project_buckets.values(), key=lambda g: g["best_rank"])
    return conv_groups, project_groups, memory_global, memory_orphans


# ---------------------------------------------------------------------------
# Upload (plan §12 step 7)
# ---------------------------------------------------------------------------


# 200 MB cap covers a hypothetical multi-batch single-zip; the real export
# is ~11 MB. Enforced both on upload and via Flask's MAX_CONTENT_LENGTH so
# pathological uploads don't stream into memory before we can reject them.
UPLOAD_MAX_BYTES = 200 * 1024 * 1024


@bp.route("/upload", methods=["GET"])
def upload_form() -> str:
    return render_template("upload.html", error=None)


@bp.route("/upload", methods=["POST"])
def upload_post():
    file = request.files.get("export_zip")
    if file is None or not file.filename:
        return render_template("upload.html", error="No file selected."), 400
    if not file.filename.lower().endswith(".zip"):
        return render_template("upload.html", error="Upload must be a .zip file."), 400

    active_zip: Path = current_app.config["ACTIVE_ZIP_PATH"]
    pickle_path: Path = current_app.config["PARSE_CACHE_PATH"]
    active_zip.parent.mkdir(parents=True, exist_ok=True)

    # Stream to a sibling tempfile then atomic-rename — avoids leaving a
    # half-written active_export.zip if the upload errors mid-stream.
    tmp = active_zip.with_suffix(".upload.tmp")
    try:
        with tmp.open("wb") as out:
            shutil.copyfileobj(file.stream, out, length=1 << 16)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return render_template("upload.html", error=f"Could not save upload: {e}"), 500

    # Validate by parsing BEFORE we replace the active zip / EXPORT. If the
    # zip is malformed, surface ExportFormatError as a friendly message and
    # leave the previous archive in place.
    try:
        new_export = load_export(tmp)
    except ExportFormatError as e:
        tmp.unlink(missing_ok=True)
        return render_template("upload.html", error=f"Couldn't parse archive: {e}"), 400

    # Hash once, before the move — the same digest that keys the pickle
    # cache, reused for the upload-history row below (A3).
    new_sha = zip_hash(tmp)

    # Commit: move tempfile into place, drop the stale parse cache, swap
    # in-memory Export, rebuild the search index. Order matters — Export
    # swap last, so a route in flight either sees the old or the new fully.
    tmp.replace(active_zip)
    invalidate_cache(pickle_path)

    db = get_db()
    index_export(db, new_export)
    current_app.config["EXPORT"] = new_export
    # A successful upload replaced the stored file, so the graceful-startup
    # banner condition clears with it (v1.1.5 Session I).
    current_app.config["STORED_EXPORT_FAILED"] = False

    # Record after the swap succeeded. Uploads always record, even when the
    # sha matches an earlier row — an explicit user action is worth logging.
    record_load(
        db,
        source="upload",
        filename=file.filename,
        zip_sha256=new_sha,
        export=new_export,
    )

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
    only purge surface — no other route auto-prunes."""
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
