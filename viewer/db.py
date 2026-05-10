"""Per-request SQLite connection management for Flask.

Routes call :func:`get_db` to obtain a connection scoped to the current
request; :func:`close_db` is registered as a ``teardown_appcontext`` handler
so the connection is closed automatically. The DB path comes from
``app.config["DB_PATH"]`` and the parent directory is created on first use.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db() -> sqlite3.Connection:
    """Return the current request's SQLite connection, opening one if needed."""
    if "db" not in g:
        path: Path = current_app.config["DB_PATH"]
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    """Close the request-scoped connection, if one was opened."""
    db = g.pop("db", None)
    if db is not None:
        db.close()
