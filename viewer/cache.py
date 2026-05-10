"""Pickle-based parse cache (plan §12 step 7).

Parsing the real-export ZIP is ~165 ms; trivial in absolute terms but it
dominates app start. The cache is a pickled :class:`viewer.parser.Export`
keyed by ``(zip_sha256, parser_module_sha256)``:

- ``zip_sha256`` — SHA256 over the zip bytes. Cheap (~ms), immune to mtime
  weirdness on Windows.
- ``parser_module_sha256`` — SHA256 of ``viewer/parser.py`` source. Simplest
  invalidation that catches parser-shape changes; doesn't try to track
  every module the parser depends on, since rebuild is cheap.

On cache miss (or hash mismatch), parse normally and re-pickle. Pickle file
format: a dict ``{"key": (zip_hash, parser_hash), "export": Export}``. No
schema versioning beyond the parser hash — if the dataclass shape changes,
the parser hash changes too.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from viewer.parser import load_export

if TYPE_CHECKING:
    from viewer.parser import Export

logger = logging.getLogger(__name__)


_PARSER_MODULE = Path(__file__).resolve().parent / "parser.py"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def parser_module_hash() -> str:
    """SHA256 of ``viewer/parser.py``. Recomputed on every call — cheap, and
    avoids stale-cache surprises during dev iteration."""
    return _sha256_file(_PARSER_MODULE)


def zip_hash(zip_path: Path) -> str:
    return _sha256_file(zip_path)


def load_or_build(zip_path: Path, pickle_path: Path) -> Export:
    """Return the parsed ``Export`` for ``zip_path``, using the on-disk
    pickle cache when its key matches, otherwise re-parsing and rewriting.

    The pickle file is written atomically via a tempfile-rename to avoid
    leaving a half-written cache on a process kill mid-write.
    """
    expected_key = (zip_hash(zip_path), parser_module_hash())

    if pickle_path.is_file():
        try:
            with pickle_path.open("rb") as f:
                blob = pickle.load(f)
            if isinstance(blob, dict) and blob.get("key") == expected_key:
                logger.info("parse cache: hit (%s)", pickle_path)
                return blob["export"]
            logger.info("parse cache: key mismatch — re-parsing")
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError) as e:
            logger.warning("parse cache: unreadable (%s) — re-parsing", e)

    export = load_export(zip_path)
    write_cache(pickle_path, expected_key, export)
    return export


def write_cache(pickle_path: Path, key: tuple[str, str], export: Export) -> None:
    """Atomic write of the parse cache: write to ``<path>.tmp`` then rename."""
    pickle_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pickle_path.with_suffix(pickle_path.suffix + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump({"key": key, "export": export}, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(pickle_path)
    logger.info("parse cache: wrote %s", pickle_path)


def invalidate(pickle_path: Path) -> None:
    """Remove the cache file if it exists. Used on upload — the new zip is
    always parsed fresh, then the cache is rewritten with the new key."""
    pickle_path.unlink(missing_ok=True)
