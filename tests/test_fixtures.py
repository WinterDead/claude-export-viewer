"""Regeneration guard for the checked-in fixture binaries (v1.3.0 Session S).

``build_fixtures.py`` writes deterministically (fixed zip timestamps, sorted
member names, stored compression), so a fresh build must byte-match the
committed files. Drift in either direction — a builder edit without a rebuild,
or a binary committed without a builder — fails here.
"""

from __future__ import annotations

from pathlib import Path

from tests.fixtures import build_fixtures


def test_checked_in_fixtures_match_fresh_build(tmp_path: Path, fixtures_dir: Path) -> None:
    written = build_fixtures.build_all(tmp_path)
    assert written, "builder produced no fixtures"
    for built in written:
        rel = built.relative_to(tmp_path)
        checked_in = fixtures_dir / rel
        assert checked_in.is_file(), f"builder output has no checked-in twin: {rel}"
        assert built.read_bytes() == checked_in.read_bytes(), f"fixture drift: {rel}"


def test_no_unregistered_fixture_binaries(tmp_path: Path, fixtures_dir: Path) -> None:
    """Every committed fixture binary must come from the registry."""
    written = {p.relative_to(tmp_path) for p in build_fixtures.build_all(tmp_path)}
    on_disk = {
        p.relative_to(fixtures_dir)
        for p in fixtures_dir.rglob("*")
        if p.is_file() and p.suffix in {".zip", ".json"}
    }
    assert on_disk == written
