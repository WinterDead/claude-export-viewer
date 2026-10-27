"""The real-data hook itself, exercised against synthetic stand-ins (v1.3.1).

``tests/real_exports.py`` discovers exports under ``VIEWER_REAL_EXPORT_DIR``; these tests
pin its environment handling and its invariant helpers without any real data.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tests import real_exports
from viewer.parser import load_export


def test_unset_variable_skips_with_a_reason_naming_it() -> None:
    found = real_exports.discover({})
    assert (found.legacy, found.sets) == ([], [])
    assert found.skip_reason == "VIEWER_REAL_EXPORT_DIR is unset"


def test_blank_variable_counts_as_unset() -> None:
    found = real_exports.discover({"VIEWER_REAL_EXPORT_DIR": "   "})
    assert found.skip_reason == "VIEWER_REAL_EXPORT_DIR is unset"


def test_missing_directory_skips_with_a_reason_naming_it(tmp_path: Path) -> None:
    found = real_exports.discover({"VIEWER_REAL_EXPORT_DIR": str(tmp_path / "absent")})
    assert (found.legacy, found.sets) == ([], [])
    assert "VIEWER_REAL_EXPORT_DIR" in found.skip_reason


def test_empty_directory_skips(tmp_path: Path) -> None:
    found = real_exports.discover({"VIEWER_REAL_EXPORT_DIR": str(tmp_path)})
    assert found.skip_reason == "VIEWER_REAL_EXPORT_DIR holds no recognisable export"


def test_directory_with_several_exports(tmp_path: Path, fixtures_dir: Path) -> None:
    shutil.copy(fixtures_dir / "synthetic_export.zip", tmp_path / "one.zip")
    shutil.copy(fixtures_dir / "synthetic_export_login_history.zip", tmp_path / "two.zip")
    # A root-level ZIP that is not a legacy archive is not a legacy case.
    shutil.copy(fixtures_dir / "current" / "projects-000.zip", tmp_path / "stray.zip")
    shutil.copytree(fixtures_dir / "current", tmp_path / "set-a")
    shutil.copytree(fixtures_dir / "current_dangling", tmp_path / "set-b")
    (tmp_path / "not-a-set").mkdir()

    found = real_exports.discover({"VIEWER_REAL_EXPORT_DIR": str(tmp_path)})
    assert found.skip_reason is None
    assert [p.name for p in found.legacy] == ["one.zip", "two.zip"]
    assert [p.name for p in found.sets] == ["set-a", "set-b"]


def test_case_ids_carry_no_names(tmp_path: Path, fixtures_dir: Path, monkeypatch) -> None:
    shutil.copytree(fixtures_dir / "current", tmp_path / "Personal Folder Name")
    monkeypatch.setenv("VIEWER_REAL_EXPORT_DIR", str(tmp_path))
    ids = [p.id for p in real_exports.params("set")]
    assert ids == ["set-1"]


def test_invariants_hold_on_the_synthetic_fixtures(fixtures_dir: Path) -> None:
    for name in ("synthetic_export.zip", "current"):
        real_exports.assert_invariants(load_export(fixtures_dir / name))


def test_parent_graph_check_counts_orphans_and_cycles(fixtures_dir: Path) -> None:
    for name in ("bad_orphan_parent.zip", "bad_cycle_two_node.zip"):
        export = load_export(fixtures_dir / name)
        assert real_exports.unresolved_parent_graphs(export) == 1


def test_unknown_block_check_ignores_truly_unknown_types(fixtures_dir: Path) -> None:
    export = load_export(fixtures_dir / "bad_unknown_block.zip")
    assert real_exports.unknown_known_types(export) == 0
