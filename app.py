"""Flask application factory and CLI entry point.

Run the dev server from the project root::

    .venv/Scripts/flask --app app run

Startup load order (plan §12 step 7):

1. ``cache/active_export.zip`` — the most-recently uploaded archive.
2. ``data/data.zip`` — dev convenience copy committed by hand.
3. ``tests/fixtures/synthetic_export.zip`` — last-ditch fallback so the
   templates always have *something* to render.

When the active zip is present and its hash matches the pickle cache key,
``viewer.cache.load_or_build`` short-circuits the parse step. ``create_app``
accepts an explicit path or ``Export`` for tests.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from viewer.cache import load_or_build, zip_hash
from viewer.db import close_db, get_db
from viewer.history import init_db as init_history_db
from viewer.history import record_startup_load
from viewer.parser import Export, load_export
from viewer.render import (
    block_type,
    conversation_display_title,
    format_local,
    format_local_relative,
    markdown_to_html,
    render_search_snippet,
    sender_label,
    slugify,
    summary_preview,
)
from viewer.routes import UPLOAD_MAX_BYTES, bp
from viewer.search import index_export
from viewer.state import compute_tag_state
from viewer.tagging import NO_PROJECT_LABEL, NO_PROJECT_SENTINEL, init_db

PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_ROOT / "cache"
ACTIVE_ZIP = CACHE_DIR / "active_export.zip"
PARSE_CACHE = CACHE_DIR / "parsed_export.pkl"
REAL_EXPORT = PROJECT_ROOT / "data" / "data.zip"
SYNTHETIC_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "synthetic_export.zip"
DB_PATH = CACHE_DIR / "viewer.sqlite"


def _resolve_default_source() -> Path:
    """Apply the documented startup-load order."""
    if ACTIVE_ZIP.is_file():
        return ACTIVE_ZIP
    if REAL_EXPORT.is_file():
        return REAL_EXPORT
    return SYNTHETIC_FIXTURE


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

    # ``(filename, zip_sha256)`` when this startup load should be considered
    # for an upload-history row — real exports only (the active zip or the
    # dev ``data/data.zip``). Fixture and test loads record nothing.
    startup_record: tuple[str, str] | None = None

    if isinstance(export_source, Export):
        export = export_source
    else:
        if export_source is None:
            export_source = _resolve_default_source()
        src = Path(export_source)
        # Use the parse cache only for the active export — fixtures and
        # the dev ``data/data.zip`` get parsed fresh every time, so test
        # runs and developer iteration aren't affected by stale pickles.
        if src.resolve() == ACTIVE_ZIP.resolve():
            # Hash once: the digest keys the pickle cache AND identifies the
            # archive in the upload-history log (A3 — no second hashing).
            sha = zip_hash(src)
            export = load_or_build(src, PARSE_CACHE, zip_sha256=sha)
            startup_record = (src.name, sha)
        elif src.resolve() == REAL_EXPORT.resolve():
            export = load_export(src)
            startup_record = (src.name, zip_hash(src))
        else:
            export = load_export(src)

    app.config["EXPORT"] = export
    app.config["DB_PATH"] = db_path if db_path is not None else DB_PATH
    app.config["ACTIVE_ZIP_PATH"] = ACTIVE_ZIP
    app.config["PARSE_CACHE_PATH"] = PARSE_CACHE
    app.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_BYTES
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
        live_export: Export = app.config["EXPORT"]
        try:
            state = compute_tag_state(get_db(), live_export)
            orphan_count = state.orphan_count
        except Exception:  # noqa: BLE001
            # Banner is informational; never block a page render on its
            # computation. Defensive against e.g. DB connection oddities.
            orphan_count = 0
        return {
            "g_user_name": live_export.user.full_name,
            "g_source_paths": list(live_export.source_paths),
            "g_orphan_count": orphan_count,
        }

    app.register_blueprint(bp)
    return app


# `flask --app app run` discovers this module-level variable.
app = create_app()
