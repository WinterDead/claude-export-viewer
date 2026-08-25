"""Route + template tests for the orphan-tag flow (plan §12 step 7).

Covers:

- ``/orphans`` empty state.
- ``/orphans`` rendering both buckets when seeded.
- The conversation-detail dropdown rendering ``(Orphan: <uuid>)`` when the
  current tag is an orphan project.
- ``/orphans/purge`` happy path (CSRF + ``confirm=yes``).
- ``/orphans/purge`` rejecting a request without the ``confirm`` field.
- The orphan banner appearing in ``base.html`` when count > 0.
"""

from __future__ import annotations

from tests.conftest import scrape_csrf
from viewer.tagging import all_tags, set_tag

_CONV_1 = "00000000-0000-0000-0000-0000000000c1"  # in synthetic export
_PROJ_A = "00000000-0000-0000-0000-0000000000a1"  # in synthetic export
_GHOST_CONV = "00000000-0000-0000-0000-0000000ghost"
_GHOST_PROJ = "00000000-0000-0000-0000-0000000gh0st"


def _seed(app, **rows):
    """Helper: open an app context and stuff rows into the tag table."""
    with app.app_context():
        from viewer.db import get_db

        db = get_db()
        for cuid, puid in rows.items():
            set_tag(db, cuid, puid)


# ---------------------------------------------------------------------------
# /orphans page
# ---------------------------------------------------------------------------


def test_orphans_view_empty(client) -> None:
    body = client.get("/orphans").get_data(as_text=True)
    assert "No orphan tags" in body


def test_orphans_view_lists_both_buckets(app, client) -> None:
    _seed(
        app,
        **{_GHOST_CONV: _PROJ_A, _CONV_1: _GHOST_PROJ},
    )
    body = client.get("/orphans").get_data(as_text=True)
    assert "Orphan conversations" in body
    assert "Orphan projects on live conversations" in body
    assert _GHOST_CONV in body
    assert _GHOST_PROJ in body


def test_orphan_banner_shown_when_orphans_exist(app, client) -> None:
    _seed(app, **{_GHOST_CONV: _PROJ_A})
    body = client.get("/").get_data(as_text=True)
    assert "orphan-banner" in body
    assert "review or purge" in body


def test_orphan_banner_absent_when_clean(client) -> None:
    body = client.get("/").get_data(as_text=True)
    assert "orphan-banner" not in body


def test_orphan_banner_count_singular(app, client) -> None:
    """The singular ``orphan tag row`` (no ``s``) renders for count == 1."""
    _seed(app, **{_GHOST_CONV: _PROJ_A})
    body = client.get("/").get_data(as_text=True)
    import re

    # Pull the banner and normalise whitespace so the assertion is robust to
    # template indentation.
    m = re.search(r'<aside class="orphan-banner".*?</aside>', body, re.DOTALL)
    assert m
    text = re.sub(r"\s+", " ", m.group(0))
    assert "1</strong> orphan tag row " in text
    assert "rows" not in text


# ---------------------------------------------------------------------------
# Conversation-detail dropdown — orphan-project rendering
# ---------------------------------------------------------------------------


def test_conv_detail_renders_orphan_option(app, client) -> None:
    _seed(app, **{_CONV_1: _GHOST_PROJ})
    body = client.get(f"/conversations/{_CONV_1}").get_data(as_text=True)
    assert f"(Orphan: {_GHOST_PROJ})" in body
    # The orphan option is the selected one — not the "(Untagged)" default.
    assert f'value="{_GHOST_PROJ}" selected' in body


def test_conv_detail_no_orphan_option_for_live_tag(app, client) -> None:
    _seed(app, **{_CONV_1: _PROJ_A})
    body = client.get(f"/conversations/{_CONV_1}").get_data(as_text=True)
    assert "(Orphan:" not in body


# ---------------------------------------------------------------------------
# /orphans/purge
# ---------------------------------------------------------------------------


def test_purge_rejects_without_confirm(app, client) -> None:
    _seed(app, **{_GHOST_CONV: _PROJ_A})
    token = scrape_csrf(client)
    r = client.post("/orphans/purge", data={"csrf_token": token})
    assert r.status_code == 400
    # Tag rows must be untouched.
    with app.app_context():
        from viewer.db import get_db

        assert _GHOST_CONV in all_tags(get_db())


def test_purge_with_confirm_deletes_orphans_only(app, client) -> None:
    _seed(
        app,
        **{
            _CONV_1: _PROJ_A,  # live — must survive
            _GHOST_CONV: _PROJ_A,  # orphan-conv — gone
            "00000000-0000-0000-0000-0000000000d1": _GHOST_PROJ,  # orphan-project — gone
        },
    )
    token = scrape_csrf(client)
    r = client.post("/orphans/purge", data={"csrf_token": token, "confirm": "yes"})
    assert r.status_code == 302
    assert "/orphans" in r.headers["Location"]

    with app.app_context():
        from viewer.db import get_db

        remaining = all_tags(get_db())
    assert remaining == {_CONV_1: _PROJ_A}


def test_purge_no_op_when_no_orphans(app, client) -> None:
    """Purge should be safe to call when nothing's orphaned — useful if the
    user double-submits or the view is stale."""
    _seed(app, **{_CONV_1: _PROJ_A})
    token = scrape_csrf(client)
    r = client.post("/orphans/purge", data={"csrf_token": token, "confirm": "yes"})
    assert r.status_code == 302
    with app.app_context():
        from viewer.db import get_db

        assert all_tags(get_db()) == {_CONV_1: _PROJ_A}
