"""Flask application factory and CLI entry point.

Run the dev server from the project root::

    .venv/Scripts/flask --app app run

Startup load order (plan §12 step 7; sets since v1.3.0):

1. ``cache/active_export/`` — the most-recently uploaded set of export
   files (record §5; a legacy ZIP upload is simply a set of one).
2. ``cache/active_export.zip`` — a stored upload from a pre-set version,
   picked up by tolerance.
3. ``data/data.zip`` — dev convenience copy committed by hand.
4. ``tests/fixtures/synthetic_export.zip`` — last-ditch fallback so the
   templates always have *something* to render.

An unparseable stored upload or dev copy falls through to the next source
with a ``WARNING`` (v1.1.5 Session I) — the file is left in place and, for
the stored upload, every page shows a banner until a successful upload
replaces it. A broken fixture still raises: it is committed code, so a
failure there is a broken build, not a user condition.

When the active zip is present and its hash matches the pickle cache key,
``viewer.cache.load_or_build`` short-circuits the parse step. ``create_app``
accepts an explicit path or ``Export`` for tests.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from viewer.cache import load_or_build, set_fingerprint, zip_hash
from viewer.db import close_db, get_db
from viewer.history import init_db as init_history_db
from viewer.history import record_startup_load
from viewer.inspect import get_inspection
from viewer.parser import ALL_CATEGORIES, Export, ExportFormatError, load_export
from viewer.render import (
    block_type,
    conversation_display_title,
    format_local,
    format_local_relative,
    markdown_to_html,
    pretty_json,
    render_search_snippet,
    sender_label,
    slugify,
    summary_preview,
)
from viewer.routes import (
    MAX_REQUEST_BYTES,
    bp,
    manifest_status_for_set,
    summarise_set_dir,
)
from viewer.search import index_export
from viewer.state import compute_tag_state
from viewer.tagging import NO_PROJECT_LABEL, NO_PROJECT_SENTINEL, init_db

PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_ROOT / "cache"
# The active export is a *set directory* since v1.3.0 (record §5) — the
# supplied files stored as received; a legacy ZIP is simply a set of one. A
# pre-set ``active_export.zip`` from earlier versions is still picked up by
# the load order below (migration by tolerance, not conversion).
ACTIVE_SET = CACHE_DIR / "active_export"
ACTIVE_ZIP = CACHE_DIR / "active_export.zip"
PARSE_CACHE = CACHE_DIR / "parsed_export.pkl"
REAL_EXPORT = PROJECT_ROOT / "data" / "data.zip"
SYNTHETIC_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "synthetic_export.zip"
DB_PATH = CACHE_DIR / "viewer.sqlite"

logger = logging.getLogger(__name__)


def _active_set_files() -> list[Path]:
    if not ACTIVE_SET.is_dir():
        return []
    return sorted(p for p in ACTIVE_SET.iterdir() if p.is_file())


def _load_default_source() -> tuple[Export, tuple[str, str] | None, bool, tuple[int, int] | None]:
    """Apply the documented startup-load order, falling through gracefully.

    Returns ``(export, startup_record, stored_export_failed,
    manifest_status)``. The stored set/upload and the dev copy are files a
    user (or developer) put there, so a parse failure logs a ``WARNING``
    naming the skipped source and falls through to the next source, leaving
    the files exactly where they are — the parse-before-replace upload flow
    is the recovery path. The fixture is committed code: a broken fixture is
    a broken build, not a user condition, so its failure still raises
    (structural invariant per the preserve-and-flag rule).
    """
    stored_export_failed = False
    set_files = _active_set_files()
    if set_files:
        # Fingerprint once: it keys the pickle cache AND identifies the set
        # in the upload-history log (A3 — no second hashing).
        fingerprint = set_fingerprint(set_files)
        try:
            export = load_or_build(ACTIVE_SET, PARSE_CACHE, fingerprint=fingerprint)
            return (
                export,
                (summarise_set_dir(ACTIVE_SET), fingerprint),
                False,
                manifest_status_for_set(ACTIVE_SET),
            )
        except ExportFormatError as e:
            logger.warning(
                "stored export set %s could not be parsed and was skipped "
                "(files left in place): %s",
                ACTIVE_SET,
                e,
            )
            stored_export_failed = True
    if ACTIVE_ZIP.is_file():
        sha = zip_hash(ACTIVE_ZIP)
        try:
            export = load_or_build(ACTIVE_ZIP, PARSE_CACHE, fingerprint=sha)
            return export, (ACTIVE_ZIP.name, sha), stored_export_failed, None
        except ExportFormatError as e:
            logger.warning(
                "stored export %s could not be parsed and was skipped " "(file left in place): %s",
                ACTIVE_ZIP,
                e,
            )
            stored_export_failed = True
    if REAL_EXPORT.is_file():
        try:
            export = load_export(REAL_EXPORT)
            return export, (REAL_EXPORT.name, zip_hash(REAL_EXPORT)), stored_export_failed, None
        except ExportFormatError as e:
            logger.warning("dev copy %s could not be parsed and was skipped: %s", REAL_EXPORT, e)
    return load_export(SYNTHETIC_FIXTURE), None, stored_export_failed, None


def create_app(
    export_source: str | Path | Export | None = None,
    *,
    db_path: Path | None = None,
) -> Flask:
    """Build a configured Flask app.

    ``export_source`` may be:

    - an already-parsed ``Export`` (used by tests),
    - a path to a zip or a directory of zips,
    - ``None`` → dev fallback (see module docstring for the load order).

    ``db_path`` defaults to ``cache/viewer.sqlite``. Tests pass an isolated
    path (often a ``tmp_path`` directory) so they don't share tag state with
    the dev DB.
    """
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )

    # ``(filename, fingerprint)`` when this startup load should be
    # considered for an upload-history row — real exports only (the active
    # set/zip or the dev ``data/data.zip``). Fixture and test loads record
    # nothing.
    startup_record: tuple[str, str] | None = None
    # True when the stored upload was skipped for a parse failure — drives
    # the persistent banner until a successful upload replaces the file.
    stored_export_failed = False
    # Expected-vs-supplied counts when the active set carries a manifest
    # (record §4's degraded-view banner).
    manifest_status: tuple[int, int] | None = None

    if isinstance(export_source, Export):
        export = export_source
    elif export_source is None:
        # Default startup: apply the load order with graceful fallthrough.
        export, startup_record, stored_export_failed, manifest_status = _load_default_source()
    else:
        # Explicit source (tests, dev calls): parse failures raise — the
        # caller named the file, so degrading silently would mask the error.
        src = Path(export_source)
        # Use the parse cache only for the active export — fixtures and
        # the dev ``data/data.zip`` get parsed fresh every time, so test
        # runs and developer iteration aren't affected by stale pickles.
        if src.resolve() == ACTIVE_ZIP.resolve():
            # Hash once: the digest keys the pickle cache AND identifies the
            # archive in the upload-history log (A3 — no second hashing).
            sha = zip_hash(src)
            export = load_or_build(src, PARSE_CACHE, fingerprint=sha)
            startup_record = (src.name, sha)
        elif src.resolve() == REAL_EXPORT.resolve():
            export = load_export(src)
            startup_record = (src.name, zip_hash(src))
        else:
            export = load_export(src)

    app.config["EXPORT"] = export
    app.config["STORED_EXPORT_FAILED"] = stored_export_failed
    app.config["MANIFEST_STATUS"] = manifest_status
    app.config["DB_PATH"] = db_path if db_path is not None else DB_PATH
    app.config["ACTIVE_SET_PATH"] = ACTIVE_SET
    app.config["ACTIVE_ZIP_PATH"] = ACTIVE_ZIP
    app.config["PARSE_CACHE_PATH"] = PARSE_CACHE
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    # Per-process secret key. Restart invalidates outstanding CSRF tokens, but
    # tokens are page-scoped — short-lived UI forms don't survive a restart
    # anyway. Localhost-only tool, no cross-instance signed-cookie concerns.
    app.config["SECRET_KEY"] = secrets.token_hex(32)

    CSRFProtect(app)

    app.teardown_appcontext(close_db)
    with app.app_context():
        db = get_db()
        init_db(db)
        init_history_db(db)
        # Build the FTS5 index from the in-memory ``Export`` (plan §12 step 6).
        # Same DB as the tagging table; transactional so route handlers never
        # see a partially-populated index.
        index_export(db, export)
        # Startup loads of a real export are logged in the upload history —
        # but only when the archive differs from the most recent row, so a
        # plain relaunch doesn't duplicate the last entry (A3).
        if startup_record is not None:
            filename, sha = startup_record
            record_startup_load(db, filename=filename, zip_sha256=sha, export=export)

    app.jinja_env.filters["markdown"] = markdown_to_html
    app.jinja_env.filters["format_local"] = format_local
    app.jinja_env.filters["format_local_relative"] = format_local_relative
    app.jinja_env.filters["display_title"] = conversation_display_title
    app.jinja_env.filters["block_type"] = block_type
    app.jinja_env.filters["summary_preview"] = summary_preview
    app.jinja_env.filters["search_snippet"] = render_search_snippet
    app.jinja_env.filters["slugify"] = slugify
    app.jinja_env.filters["sender_label"] = sender_label
    app.jinja_env.filters["pretty_json"] = pretty_json

    # No-Project sentinel (v1.1 Session B): templates render the option value
    # and label from the same constants the tagging layer defines, so the
    # sentinel is spelled in exactly one place.
    app.jinja_env.globals["NO_PROJECT_SENTINEL"] = NO_PROJECT_SENTINEL
    app.jinja_env.globals["NO_PROJECT_LABEL"] = NO_PROJECT_LABEL

    # Inject only the small dict the layout chrome needs — never the full
    # ``Export`` or ``User`` objects. Otherwise a careless ``{{ export }}``
    # in a future template would dump the entire model into the page.
    @app.context_processor
    def _inject_globals() -> dict[str, object]:
        # Re-read EXPORT each request so the orphan banner reflects the
        # post-upload state without a server restart. Cheap (object refs,
        # no parsing).
        primary_export: Export = app.config["EXPORT"]
        # Inspect mode (v1.2 Session K): the footer chrome follows the view
        # (the inspected export's user and filename), but orphan state stays
        # pinned to the primary export — tags always resolve against YOUR
        # archive, so the banner count must not shift while inspecting.
        inspect_state = get_inspection(app)
        live_export = inspect_state.export if inspect_state is not None else primary_export
        try:
            state = compute_tag_state(get_db(), primary_export)
            orphan_count = state.orphan_count
        except Exception:  # noqa: BLE001
            # Banner is informational; never block a page render on its
            # computation. Defensive against e.g. DB connection oddities.
            orphan_count = 0
        # Degraded-view banner (record §4, copy C1): which of the four
        # categories the viewed set is missing, plus the manifest's
        # expected-vs-supplied counts when one was supplied. Follows the
        # view — an inspected subset is described as truthfully as the
        # active one.
        missing = [c for c in ALL_CATEGORIES if c not in live_export.categories]
        if inspect_state is not None:
            manifest_status = inspect_state.manifest_status
        else:
            manifest_status = app.config.get("MANIFEST_STATUS")

        return {
            # ``user`` is None when a current-format set lacks its account
            # file (record §3) — the chrome degrades to an empty name.
            "g_user_name": live_export.user.full_name if live_export.user is not None else "",
            "g_source_paths": list(live_export.source_paths),
            "g_orphan_count": orphan_count,
            # Re-read per request: a successful upload clears the flag
            # without a restart (viewer.routes.upload_post).
            "g_stored_export_failed": app.config.get("STORED_EXPORT_FAILED", False),
            "g_inspecting": inspect_state is not None,
            "g_inspect_filename": inspect_state.filename if inspect_state is not None else "",
            "g_missing_categories": missing,
            "g_loaded_category_count": len(ALL_CATEGORIES) - len(missing),
            "g_manifest_status": manifest_status,
            # Nav rule (record §8): the Account entry shows whenever
            # users.json is in the viewed set, both formats.
            "g_has_account": live_export.user is not None,
        }

    app.register_blueprint(bp)
    return app


# `flask --app app run` discovers this module-level variable.
app = create_app()
