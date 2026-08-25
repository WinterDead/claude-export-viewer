"""Tests for the pickle-based parse cache (plan §12 step 7).

Covers cache hit, key mismatch (zip changed, parser changed), corrupted
file, atomic write, and invalidation.
"""

from __future__ import annotations

import pickle
from pathlib import Path

from viewer.cache import (
    invalidate,
    load_or_build,
    parser_module_hash,
    write_cache,
    zip_hash,
)
from viewer.parser import Export, load_export


def test_first_call_builds_cache(tmp_path: Path, export_path: Path) -> None:
    pkl = tmp_path / "parsed.pkl"
    assert not pkl.exists()
    export = load_or_build(export_path, pkl)
    assert isinstance(export, Export)
    assert pkl.is_file()


def test_second_call_hits_cache(tmp_path: Path, export_path: Path, monkeypatch) -> None:
    """If the key matches, ``load_export`` should NOT be called the second time."""
    pkl = tmp_path / "parsed.pkl"
    load_or_build(export_path, pkl)

    sentinel: list[int] = []

    def boom(*_a, **_kw):
        sentinel.append(1)
        raise AssertionError("load_export must not be called on cache hit")

    monkeypatch.setattr("viewer.cache.load_export", boom)
    export = load_or_build(export_path, pkl)
    assert isinstance(export, Export)
    assert sentinel == []


def test_key_mismatch_zip_changed_rebuilds(
    tmp_path: Path, export_path: Path, fixtures_dir: Path
) -> None:
    """Pointing at a different zip with the same pickle path triggers a rebuild."""
    pkl = tmp_path / "parsed.pkl"
    load_or_build(export_path, pkl)

    other_zip = fixtures_dir / "bad_dangling_memory.zip"
    export = load_or_build(other_zip, pkl)
    # Should reflect the new archive's content — orphan memory present.
    assert len(export.orphan_project_memories) >= 1


def test_key_mismatch_parser_hash_invalidates(
    tmp_path: Path, export_path: Path, monkeypatch
) -> None:
    """If the parser-module hash changes, the cache is rebuilt."""
    pkl = tmp_path / "parsed.pkl"
    load_or_build(export_path, pkl)

    monkeypatch.setattr("viewer.cache.parser_module_hash", lambda: "f" * 64)

    rebuilt: list[int] = []
    real_load = load_export

    def tracking_load(*a, **kw):
        rebuilt.append(1)
        return real_load(*a, **kw)

    monkeypatch.setattr("viewer.cache.load_export", tracking_load)
    load_or_build(export_path, pkl)
    assert rebuilt == [1]


def test_corrupted_pickle_falls_back_to_parse(tmp_path: Path, export_path: Path) -> None:
    pkl = tmp_path / "parsed.pkl"
    pkl.write_bytes(b"not a pickle, not even close")
    export = load_or_build(export_path, pkl)
    assert isinstance(export, Export)
    # And the cache was rewritten with the valid blob.
    with pkl.open("rb") as f:
        blob = pickle.load(f)
    assert isinstance(blob, dict)
    assert "export" in blob


def test_write_is_atomic_no_tmp_left_behind(tmp_path: Path, export_path: Path) -> None:
    pkl = tmp_path / "parsed.pkl"
    export = load_export(export_path)
    key = (zip_hash(export_path), parser_module_hash())
    write_cache(pkl, key, export)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    assert pkl.is_file()


def test_invalidate_removes_file_idempotent(tmp_path: Path) -> None:
    pkl = tmp_path / "parsed.pkl"
    pkl.write_bytes(b"x")
    invalidate(pkl)
    assert not pkl.exists()
    # Second call must not raise.
    invalidate(pkl)


def test_zip_hash_stable_across_calls(export_path: Path) -> None:
    a = zip_hash(export_path)
    b = zip_hash(export_path)
    assert a == b
    assert len(a) == 64  # SHA256 hex


def test_parser_module_hash_stable() -> None:
    a = parser_module_hash()
    b = parser_module_hash()
    assert a == b
    assert len(a) == 64
