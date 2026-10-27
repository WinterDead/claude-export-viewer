"""Privacy and behaviour guards for ``tools/scan_export_schema.py`` (v1.4.0 Session U).

The scanner is the only code allowed to read a real export, so its output rules are
pinned here against synthetic fixtures: nothing but aggregates, type names, key names,
and allowlisted enums under the cardinality cap may ever print.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from tests.fixtures import build_fixtures as bf
from tools import scan_export_schema as scanner


def _report_text(path: Path) -> str:
    report = scanner.scan(path)
    return scanner.format_report(report) + "\n" + json.dumps(report)


@pytest.fixture
def multi_export_dir(tmp_path: Path, fixtures_dir: Path) -> Path:
    """A directory shaped like an operator's export folder: a legacy ZIP at the root
    and two current-format sets in subdirectories with personal-looking names."""
    root = tmp_path / "Private Exports Folder"
    root.mkdir()
    shutil.copy(fixtures_dir / "synthetic_export_login_history.zip", root / "legacy-export.zip")
    first = root / "Personal Set October"
    shutil.copytree(fixtures_dir / "current", first)
    second = root / "Personal Set Later"
    shutil.copytree(fixtures_dir / "current_new_only", second)
    shutil.copy(fixtures_dir / "current" / "memories-000.zip", second / "memories-000.zip")
    return root


def test_source_is_not_an_allowlisted_enum() -> None:
    # display_content.link.source carries website names (a chat-side scan leaked them).
    assert "source" not in scanner.ENUM_FIELDS
    assert not any(path.endswith(".source") for path in scanner.SCOPED_ENUM_PATHS)


def test_link_source_values_never_print(fixtures_dir: Path) -> None:
    text = _report_text(fixtures_dir / "current")
    assert "Example" not in text  # the fixture's _link_dict source value


SYNTHETIC_CONTENT = [
    # Artifact titles, descriptions, bodies, and file paths inside artifacts.
    "Synthetic colour card",
    "Synthetic reading log",
    "Second draft",
    "Synthetic shared notes",
    "shared-notes",
    "entries.md",
    "about.md",
    "style.css",
    "capability.json",
    "fonts.example.invalid",
    # Comment text and quoted text.
    "Synthetic resolved remark",
    "First point",
    # Artifact data: collection names, document ids, data keys and values.
    "log_entries",
    "log_settings",
    "entry-0001",
    "book",
    "page_count",
    "Synthetic Title One",
    # Scheduled tasks: names, prompts, cron expressions, ids.
    "Synthetic weekly tidy-up",
    "Review the synthetic repository",
    "0 9 * * 1",
    "trig_01SYNTHETIC",
    "cse_01SYNTHETIC",
    # Conversation-side additions.
    "Synthetic brief.pdf",
    "Published artifact",
]


def test_new_categories_print_no_content(fixtures_dir: Path) -> None:
    text = _report_text(fixtures_dir / "current_new_only")
    for needle in SYNTHETIC_CONTENT:
        assert needle not in text, f"scanner printed content: {needle!r}"


def test_multi_export_directory_profiles_each_export_without_names(
    multi_export_dir: Path,
) -> None:
    report = scanner.scan(multi_export_dir)
    assert [e["kind"] for e in report["exports"]] == ["legacy", "set", "set"]
    text = _report_text(multi_export_dir)
    for needle in ("Private Exports Folder", "Personal Set", "legacy-export"):
        assert needle not in text
    for needle in SYNTHETIC_CONTENT:
        assert needle not in text, f"scanner printed content: {needle!r}"
    # V20 compares memories across exports without printing digests.
    assert any("conversations_memory identical" in line for line in report["cross_export"])
    assert not any(len(word) == 64 for line in report["cross_export"] for word in line.split())
    # V21: the legacy archive's root login history is recognised.
    checks = report["exports"][0]["checks"]
    assert checks["V21 legacy archive with root login_history.json"] == 1


def test_scoped_enums_print_only_at_their_paths(fixtures_dir: Path) -> None:
    report = scanner.scan(fixtures_dir / "current_new_only")
    frames = report["exports"][0]["containers"]["frames"]
    schema = frames["artifacts/<uuid>/artifact.json"]["schema"]
    assert schema["$.kind"]["value_census"] == {"artifact": 2, "page": 1}
    assert schema["$.visibility"]["value_census"] == {"private": 3}
    listing = frames["artifacts/<uuid>/versions/<version-id>.files.json"]["schema"]
    assert "text/markdown" in listing["$.files[].mime_type"]["value_census"]
    # A title is never a census, even at a scoped document.
    assert "value_census" not in schema["$.versions[].title"]


def test_artifact_data_keys_are_opaque(fixtures_dir: Path) -> None:
    report = scanner.scan(fixtures_dir / "current_new_only")
    data = report["exports"][0]["containers"]["artifact_data"]
    schema = data["artifacts/<uuid>/data/<masked>.json"]["schema"]
    assert not any(path.startswith("$.documents[].data.") for path in schema)
    assert "dict(opaque)" in schema["$.documents[].data"]["types"]


def test_cardinality_cap_suppresses_many_valued_enums(tmp_path: Path) -> None:
    conversations = [
        {
            "uuid": "00000000-0000-0000-0000-000000000001",
            "chat_messages": [
                {"sender": "human", "content": [{"type": f"kind{i}"} for i in range(13)]}
            ],
        }
    ]
    archive = tmp_path / "legacy.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conversations))
        zf.writestr("users.json", "[]")
        zf.writestr("memories.json", "[]")
    report = scanner.scan(archive)
    schema = report["exports"][0]["containers"]["legacy"]["conversations.json"]["schema"]
    stats = schema["$[].chat_messages[].content[].type"]
    assert "value_census" not in stats
    assert stats["value_census_suppressed"] == 13
    assert "kind7" not in _report_text(archive)


def test_structural_segments_print_and_unique_ones_mask() -> None:
    structural = {"versions", "artifact.json"}
    assert scanner._mask_segment("versions", structural) == "versions"
    assert scanner._mask_segment("my-private-notes.md", structural) == "<masked>.md"
    assert scanner._mask_segment("1790000000-e5f6.files.json", structural) == (
        "<version-id>.files.json"
    )
    assert scanner._mask_segment("1790000000-e5f6", structural) == "<version-id>"
    assert scanner._mask_segment(bf.ARTIFACT_MULTI_UUID, structural) == "<uuid>"


def test_integrity_checks_detect_the_edge_set(fixtures_dir: Path) -> None:
    checks = scanner.scan(fixtures_dir / "current_frames_edge")["exports"][0]["checks"]
    assert checks["V8 listed files sha256 matches False"] == 1
    assert checks["V8 listed files size matches False"] == 1
    assert checks["V8 listed files missing from the ZIP"] == 1
    assert checks["V8 files present but unlisted"] == 1
    assert checks["V7 active_version in versions list False"] == 2
