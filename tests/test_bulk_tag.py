"""Route-level tests for the bulk-tagging endpoint (plan §12 step 4.5).

Goes through the Flask test client (rather than calling tagging.py directly)
because the value of these tests is in the route's mode-handling, validation,
and CSRF wiring — none of which live in the SQLite layer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from tests.conftest import DUAL_FORMAT_IDS, DUAL_FORMATS, scrape_csrf


@pytest.fixture(params=DUAL_FORMATS, ids=DUAL_FORMAT_IDS)
def export_path(request, fixtures_dir: Path) -> Path:
    """Dual-format module (v1.3.0 Session S): export-dependent tests in this
    file run against both container formats."""
    return fixtures_dir / request.param


# Synthetic UUIDs — match the happy-path fixture.
ACCOUNT = "00000000-0000-0000-0000-000000000001"
PROJ_A = "00000000-0000-0000-0000-0000000000a1"
PROJ_B = "00000000-0000-0000-0000-0000000000b1"
CONV_1 = "00000000-0000-0000-0000-0000000000c1"
CONV_2 = "00000000-0000-0000-0000-0000000000d1"
CONV_3 = "00000000-0000-0000-0000-0000000000e1"
CONV_4 = "00000000-0000-0000-0000-0000000000f1"


def _current_tag(client, conv_uuid: str) -> str | None:
    """Read the dropdown's selected option for a conversation from the rendered list."""
    body = client.get("/conversations").get_data(as_text=True)
    row = re.search(
        r'value="' + re.escape(conv_uuid) + r'"[\s\S]*?</li>',
        body,
    )
    assert row, f"no row for {conv_uuid}"
    # The macro renders attribute and ``selected`` on different lines, so the
    # whitespace between them is variable — match with ``\s+``.
    selected = re.search(r'<option value="([^"]*)"\s+selected', row.group(0))
    if not selected:
        return None
    return selected.group(1) or None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_bulk_rejects_missing_csrf_token(client) -> None:
    r = client.post("/tag/bulk", data={"mode": "tag", "project_uuid": PROJ_A})
    assert r.status_code == 400


def test_bulk_rejects_invalid_mode(client) -> None:
    r = client.post(
        "/tag/bulk",
        data={"csrf_token": scrape_csrf(client), "mode": "delete", "project_uuid": PROJ_A},
    )
    assert r.status_code == 400


def test_bulk_rejects_unknown_conversation(client) -> None:
    r = client.post(
        "/tag/bulk",
        data=MultiDict(
            [
                ("csrf_token", scrape_csrf(client)),
                ("mode", "tag"),
                ("project_uuid", PROJ_A),
                ("conversation_uuid", "00000000-0000-0000-0000-00000000bad0"),
            ]
        ),
    )
    assert r.status_code == 400


def test_bulk_rejects_unknown_project(client) -> None:
    r = client.post(
        "/tag/bulk",
        data=MultiDict(
            [
                ("csrf_token", scrape_csrf(client)),
                ("mode", "tag"),
                ("project_uuid", "00000000-0000-0000-0000-00000000bad0"),
                ("conversation_uuid", CONV_1),
            ]
        ),
    )
    assert r.status_code == 404


def test_bulk_rejects_tag_mode_without_project(client) -> None:
    r = client.post(
        "/tag/bulk",
        data=MultiDict(
            [
                ("csrf_token", scrape_csrf(client)),
                ("mode", "tag"),
                ("conversation_uuid", CONV_1),
            ]
        ),
    )
    assert r.status_code == 400


def test_bulk_with_no_selection_is_no_op_redirect(client) -> None:
    r = client.post(
        "/tag/bulk",
        data={
            "csrf_token": scrape_csrf(client),
            "mode": "tag",
            "project_uuid": PROJ_A,
        },
    )
    assert r.status_code == 302


# ---------------------------------------------------------------------------
# Tag mode — skips already-tagged
# ---------------------------------------------------------------------------


def _post_bulk(client, mode, *, conv_uuids, project_uuid=""):
    pairs = [
        ("csrf_token", scrape_csrf(client)),
        ("mode", mode),
        ("project_uuid", project_uuid),
    ]
    pairs.extend(("conversation_uuid", u) for u in conv_uuids)
    return client.post("/tag/bulk", data=MultiDict(pairs))


def test_tag_mode_tags_untagged_rows(client) -> None:
    r = _post_bulk(client, "tag", conv_uuids=[CONV_1, CONV_2], project_uuid=PROJ_A)
    assert r.status_code == 302
    assert _current_tag(client, CONV_1) == PROJ_A
    assert _current_tag(client, CONV_2) == PROJ_A


def test_tag_mode_skips_already_tagged_rows(client) -> None:
    # Pre-tag conv_1 to A.
    _post_bulk(client, "tag", conv_uuids=[CONV_1], project_uuid=PROJ_A)
    # Bulk-tag both to B in tag mode → conv_1 should remain at A.
    _post_bulk(client, "tag", conv_uuids=[CONV_1, CONV_2], project_uuid=PROJ_B)
    assert _current_tag(client, CONV_1) == PROJ_A
    assert _current_tag(client, CONV_2) == PROJ_B


# ---------------------------------------------------------------------------
# Retag mode — overwrites
# ---------------------------------------------------------------------------


def test_retag_mode_overwrites_existing_tags(client) -> None:
    _post_bulk(client, "tag", conv_uuids=[CONV_1], project_uuid=PROJ_A)
    _post_bulk(client, "retag", conv_uuids=[CONV_1, CONV_2], project_uuid=PROJ_B)
    assert _current_tag(client, CONV_1) == PROJ_B
    assert _current_tag(client, CONV_2) == PROJ_B


# ---------------------------------------------------------------------------
# Untag mode
# ---------------------------------------------------------------------------


def test_untag_mode_clears_selected(client) -> None:
    _post_bulk(client, "tag", conv_uuids=[CONV_1, CONV_2], project_uuid=PROJ_A)
    _post_bulk(client, "untag", conv_uuids=[CONV_1])
    assert _current_tag(client, CONV_1) is None
    assert _current_tag(client, CONV_2) == PROJ_A


def test_untag_mode_does_not_require_project(client) -> None:
    _post_bulk(client, "tag", conv_uuids=[CONV_1], project_uuid=PROJ_A)
    r = client.post(
        "/tag/bulk",
        data=MultiDict(
            [
                ("csrf_token", scrape_csrf(client)),
                ("mode", "untag"),
                ("conversation_uuid", CONV_1),
            ]
        ),
    )
    assert r.status_code == 302
    assert _current_tag(client, CONV_1) is None
