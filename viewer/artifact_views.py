"""Artifacts and Scheduled tasks pages, and the sandboxed preview (v1.4.0).

``docs/export-categories-design.md`` §6.2–§6.5, copy D8/D9. Registered on the main
blueprint. Everything here reads the viewed export — the inspected one while inspecting —
and writes nothing anywhere: artifact bytes come from :func:`viewer.categories.read_member`
through :func:`viewer.routes.artifact_part_opener`, and downloads are assembled in memory.

**The security boundary (record §6.3).** Artifact HTML never passes through the
sanitiser; it is isolated instead:

1. the preview is an ``<iframe sandbox="">`` — neither ``allow-scripts`` nor
   ``allow-same-origin``;
2. every artifact-file response carries :data:`ARTIFACT_CSP` (no remote origin for any
   fetch directive, no scripts, the ``sandbox`` directive — so the file is sandboxed
   even when opened directly in a tab), ``X-Content-Type-Options: nosniff``, and the
   file's recorded MIME type;
3. files are served only by lookup in the version's recorded file list (or the single
   HTML): the request path is compared to the list and never joined to a filesystem
   path, so traversal, absolute, encoded, backslash, and unlisted paths are all 404;
4. relative assets load from the export; anything remote is blocked by the CSP.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

from flask import Response, abort, current_app, render_template, request, url_for

from viewer.categories import (
    LAYOUT_MULTI_FILE,
    LAYOUT_NONE,
    LAYOUT_SINGLE_HTML,
    Artifact,
    ArtifactVersion,
    artifact_ids_in,
    html_has_external_resource,
    html_has_script,
    orphan_runs,
    read_member,
    version_member,
)
from viewer.inspect import get_inspection
from viewer.parser import CATEGORY_CLAUDE_CODE_CLOUD, CATEGORY_FRAMES, Export
from viewer.render import fold_line_count, slugify
from viewer.routes import _current_export, artifact_part_opener, bp

# Record §6.3, outcome 2. ``'self'`` is this local viewer, never a remote origin; the
# ``sandbox`` directive (no tokens) applies the strictest iframe sandbox to the file
# itself, wherever it's opened.
ARTIFACT_CSP = (
    "default-src 'none'; "
    "script-src 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "media-src 'self'; "
    "connect-src 'none'; "
    "frame-src 'none'; "
    "child-src 'none'; "
    "worker-src 'none'; "
    "manifest-src 'none'; "
    "object-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'self'; "
    "sandbox"
)

# The preview iframe's sandbox attribute: empty — every restriction applies.
PREVIEW_SANDBOX = ""

# Copy D8: type and visibility labels; unknown values show raw.
TYPE_LABELS = {"artifact": "Artifact", "page": "Document"}
VISIBILITY_LABELS = {"private": "Private"}

ARTIFACT_SORTS = ("updated_desc", "created_desc", "title_asc")
_EPOCH = datetime.min.replace(tzinfo=UTC)


def type_label(kind: str) -> str:
    return TYPE_LABELS.get(kind, kind)


def visibility_label(visibility: str) -> str:
    return VISIBILITY_LABELS.get(visibility, visibility)


def _artifacts_or_404(export: Export) -> None:
    if CATEGORY_FRAMES not in export.categories and not export.artifact_data:
        abort(404)


def _find_artifact(export: Export, artifact_id: str) -> Artifact:
    for artifact in export.artifacts:
        if artifact.id == artifact_id:
            return artifact
    abort(404)


def _find_version(artifact: Artifact, version_id: str) -> ArtifactVersion:
    version = artifact.version(version_id)
    if version is None:
        abort(404)
    return version


def _read(artifact: Artifact, member: str) -> bytes | None:
    return read_member(artifact_part_opener(), artifact.part, member)


def _download_name(artifact: Artifact, suffix: str) -> str:
    return f"{slugify(artifact.title or artifact.id)}{suffix}"


# ---------------------------------------------------------------------------
# Artifacts list (record §6.2, §6.4 orphans)
# ---------------------------------------------------------------------------


@bp.route("/artifacts")
def artifact_list() -> str:
    export = _current_export()
    _artifacts_or_404(export)
    sort = request.args.get("sort", "updated_desc")
    if sort not in ARTIFACT_SORTS:
        sort = "updated_desc"
    if sort == "title_asc":
        artifacts = sorted(export.artifacts, key=lambda a: (a.title.lower(), a.id))
    elif sort == "created_desc":
        artifacts = sorted(
            export.artifacts, key=lambda a: (a.created_at or _EPOCH, a.id), reverse=True
        )
    else:
        artifacts = sorted(
            export.artifacts, key=lambda a: (a.updated_at or _EPOCH, a.id), reverse=True
        )
    known = {a.id for a in export.artifacts}
    orphans = [d for d in export.artifact_data if d.artifact_id not in known]
    return render_template(
        "artifacts.html",
        artifacts=artifacts,
        orphan_groups=_data_groups(orphans),
        sort=sort,
        type_label=type_label,
    )


def _data_groups(files) -> list[dict]:
    """Saved data grouped by collection (record §6.4), documents in file order."""
    groups: dict[str, list] = {}
    for data_file in files:
        for doc in data_file.documents:
            groups.setdefault(doc.collection, []).append(doc)
    return [{"collection": name, "documents": docs} for name, docs in sorted(groups.items())]


# ---------------------------------------------------------------------------
# Artifact detail (record §6.2–§6.5)
# ---------------------------------------------------------------------------


@bp.route("/artifacts/<artifact_id>")
def artifact_detail(artifact_id: str) -> str:
    export = _current_export()
    _artifacts_or_404(export)
    artifact = _find_artifact(export, artifact_id)

    requested = request.args.get("version")
    version = _find_version(artifact, requested) if requested else artifact.active

    preview = None
    content_files: list[dict] = []
    if version is not None and version.layout in (LAYOUT_SINGLE_HTML, LAYOUT_MULTI_FILE):
        entry = f"{version.id}.html" if version.layout == LAYOUT_SINGLE_HTML else version.entry_path
        if entry is not None:
            html = _read(artifact, _member_for(artifact, version, entry)) or b""
            source = html.decode("utf-8", errors="replace")
            preview = {
                "src": url_for(
                    "viewer.artifact_file",
                    artifact_id=artifact.id,
                    version_id=version.id,
                    subpath=entry,
                ),
                "scripts": html_has_script(html),
                "external": html_has_external_resource(html),
                "source": source,
                # A5's rule governs View source too (record §6.2).
                "source_fold_lines": fold_line_count(source),
            }
        if version.layout == LAYOUT_MULTI_FILE:
            for f in version.files:
                if f.mime_type == "text/markdown" and f.present:
                    body = _read(artifact, version_member(artifact.id, version, f.path))
                    content_files.append(
                        {"path": f.path, "body": (body or b"").decode("utf-8", errors="replace")}
                    )

    page_markdown = None
    if artifact.content_member is not None:
        raw = _read(artifact, artifact.content_member)
        page_markdown = raw.decode("utf-8", errors="replace") if raw is not None else None

    saved = [d for d in export.artifact_data if d.artifact_id == artifact.id]
    conv_by_uuid = {c.uuid: c for c in export.conversations}
    mentions = [
        conv_by_uuid[u] for u in export.artifact_mentions.get(artifact.id, []) if u in conv_by_uuid
    ]
    return render_template(
        "artifact_detail.html",
        artifact=artifact,
        version=version,
        preview=preview,
        content_files=content_files,
        page_markdown=page_markdown,
        data_groups=_data_groups(saved),
        mentions=mentions,
        type_label=type_label,
        visibility_label=visibility_label,
        sandbox=PREVIEW_SANDBOX,
        LAYOUT_NONE=LAYOUT_NONE,
        LAYOUT_MULTI_FILE=LAYOUT_MULTI_FILE,
    )


def _member_for(artifact: Artifact, version: ArtifactVersion, subpath: str) -> str | None:
    """The ZIP member a request path names — by lookup only (record §6.3, outcome 3).

    The single-HTML version serves exactly one name; a multi-file version serves only
    paths in its recorded file list that are present in the export. Anything else —
    traversal, absolute, encoded, backslashed, unlisted — finds nothing.
    """
    if version.layout == LAYOUT_SINGLE_HTML:
        return version_member(artifact.id, version) if subpath == f"{version.id}.html" else None
    if version.layout == LAYOUT_MULTI_FILE:
        listed = version.file(subpath)
        if listed is not None and listed.present:
            return version_member(artifact.id, version, listed.path)
    return None


def _mime_for(version: ArtifactVersion, subpath: str) -> str:
    if version.layout == LAYOUT_SINGLE_HTML:
        return "text/html"
    listed = version.file(subpath)
    if listed is not None and listed.mime_type:
        return listed.mime_type
    return "application/octet-stream"


# ``merge_slashes=False``: an absolute-looking request path (``f//etc/hosts``) is
# looked up as given and 404s, rather than being redirected to a merged path.
@bp.route("/artifacts/<artifact_id>/v/<version_id>/f/<path:subpath>", merge_slashes=False)
def artifact_file(artifact_id: str, version_id: str, subpath: str) -> Response:
    """One artifact file, sandboxed by its own response headers (record §6.3)."""
    export = _current_export()
    artifact = _find_artifact(export, artifact_id)
    version = _find_version(artifact, version_id)
    member = _member_for(artifact, version, subpath)
    if member is None:
        abort(404)
    data = _read(artifact, member)
    if data is None:
        abort(404)
    resp = Response(data, mimetype=_mime_for(version, subpath))
    resp.headers["Content-Security-Policy"] = ARTIFACT_CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    if resp.mimetype.startswith("font/"):
        # The sandboxed preview has an opaque origin, and browsers fetch web fonts in
        # CORS mode — without this, a relative font in the export can't load (outcome
        # 4). ``null`` admits only opaque origins, and fonts carry no credentials.
        resp.headers["Access-Control-Allow-Origin"] = "null"
    return resp


# ---------------------------------------------------------------------------
# Downloads — assembled in memory, nothing written server-side (record §6.2)
# ---------------------------------------------------------------------------


def _attachment(data: bytes, mimetype: str, filename: str) -> Response:
    resp = Response(data, mimetype=mimetype)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/artifacts/<artifact_id>/v/<version_id>/download")
def artifact_download(artifact_id: str, version_id: str) -> Response:
    export = _current_export()
    artifact = _find_artifact(export, artifact_id)
    version = _find_version(artifact, version_id)
    if version.layout == LAYOUT_SINGLE_HTML:
        data = _read(artifact, version_member(artifact.id, version))
        if data is None:
            abort(404)
        return _attachment(data, "text/html", _download_name(artifact, f"-{version.id}.html"))
    if version.layout == LAYOUT_MULTI_FILE:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in version.files:
                if not f.present:
                    continue
                data = _read(artifact, version_member(artifact.id, version, f.path))
                if data is not None:
                    zf.writestr(_safe_zip_path(f.original_path or f.path), data)
        return _attachment(
            buf.getvalue(), "application/zip", _download_name(artifact, f"-{version.id}.zip")
        )
    abort(404)


_SAFE_SEGMENT_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]+$")


def _safe_zip_path(path: str) -> str:
    """A ZIP member name built from the recorded path, never escaping its root."""
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    parts = [p if _SAFE_SEGMENT_RE.match(p) else "_" for p in parts]
    return "/".join(parts) or "file"


@bp.route("/artifacts/<artifact_id>/download")
def artifact_page_download(artifact_id: str) -> Response:
    """A ``page`` artifact downloads as its Markdown (record §6.2)."""
    export = _current_export()
    artifact = _find_artifact(export, artifact_id)
    if artifact.content_member is None:
        abort(404)
    data = _read(artifact, artifact.content_member)
    if data is None:
        abort(404)
    return _attachment(data, "text/markdown", _download_name(artifact, ".md"))


# ---------------------------------------------------------------------------
# Scheduled tasks (record §6, copy D9)
# ---------------------------------------------------------------------------


@bp.route("/scheduled-tasks")
def scheduled_tasks() -> str:
    export = _current_export()
    if CATEGORY_CLAUDE_CODE_CLOUD not in export.categories:
        abort(404)
    runs_by_task: dict[str, list] = {}
    for run in export.task_runs:
        runs_by_task.setdefault(run.routine_id, []).append(run)
    for runs in runs_by_task.values():
        runs.sort(key=lambda r: (r.scheduled_for or _EPOCH), reverse=True)
    tasks = sorted(
        export.scheduled_tasks, key=lambda t: (t.created_at or _EPOCH, t.routine_id), reverse=True
    )
    orphans: dict[str, list] = {}
    for run in orphan_runs(export.scheduled_tasks, export.task_runs):
        orphans.setdefault(run.routine_id, []).append(run)
    return render_template(
        "scheduled_tasks.html",
        tasks=tasks,
        runs_by_task=runs_by_task,
        orphan_groups=sorted(orphans.items()),
    )


# ---------------------------------------------------------------------------
# Template helpers: nav rules and the reading view's tool-card link
# ---------------------------------------------------------------------------


def artifact_refs(block, known: frozenset[str]) -> list[str]:
    """Loaded artifacts a tool block names in ``input`` or ``structured_content``
    (record §6.5) — the reading view's **Open in Artifacts** links."""
    value = getattr(block, "input", None)
    if value is None:
        value = getattr(block, "structured_content", None)
    return artifact_ids_in(value, known)


def nav_flags(export: Export) -> dict[str, bool]:
    """Nav show-when rules (record §6.2, D9): each entry shows only when the viewed set
    includes its category — the Account page's rule."""
    return {
        "g_has_artifacts": CATEGORY_FRAMES in export.categories,
        "g_has_scheduled_tasks": CATEGORY_CLAUDE_CODE_CLOUD in export.categories,
    }


def viewed_artifact_ids() -> frozenset[str]:
    state = get_inspection(current_app)
    export = state.export if state is not None else current_app.config["EXPORT"]
    return frozenset(a.id for a in export.artifacts)
