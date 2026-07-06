"""End-to-end export-form tests (v1.2.5 Session O, B2/B3).

The operator's v1.2.0 browser pass reported the export toggles dead. The
Session O reproduction — real rendered markup, browser-order serialisation,
and a driven Chromium — found them working on every surface; what the earlier
endpoint tests never did was exercise the real form (they built their
parameters by hand — the meta-gap the Session O brief names). These tests
close that class: every request here is built from the template's actual
rendered markup by a browser-faithful serialiser (tree order, successful
controls only), for all four checkbox states, on the Export, Copy, and
bulk-export paths — plus the Session O control-grouping pins (fieldset and
byte-exact legend, Print / Save as PDF outside the group).
"""

from __future__ import annotations

import io
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

import pytest

from app import create_app

CONV_MINIMAL = "00000000-0000-0000-0000-0000000000c1"
CONV_THINKING = "00000000-0000-0000-0000-0000000000d1"
CONV_ATTACHMENT = "00000000-0000-0000-0000-0000000000e1"

# The four checkbox states: (Include thinking blocks, Include attachment text).
STATES = [(True, True), (True, False), (False, True), (False, False)]
STATE_IDS = ["think-on_att-on", "think-on_att-off", "think-off_att-on", "think-off_att-off"]


@pytest.fixture
def client(fixtures_dir: Path, tmp_path: Path):
    app = create_app(
        fixtures_dir / "synthetic_export.zip",
        db_path=tmp_path / "viewer.sqlite",
    )
    return app.test_client()


# ---------------------------------------------------------------------------
# Browser-faithful form serialisation
# ---------------------------------------------------------------------------


class _FormControls(HTMLParser):
    """Collect one form's submittable controls in document order.

    Handles both nested controls and controls associated from elsewhere in
    the document via the ``form="<id>"`` attribute (the conversations list's
    row checkboxes). Buttons are deliberately ignored — none of the export
    submitters carry a name, so a real submission includes no button pair.
    """

    def __init__(self, form_id: str) -> None:
        super().__init__()
        self.form_id = form_id
        self.action: str | None = None
        self.method: str | None = None
        self.controls: list[dict] = []
        self._in_form = False
        self._select: dict | None = None

    def _owned(self, attrs: dict) -> bool:
        if "form" in attrs:
            return attrs.get("form") == self.form_id
        return self._in_form

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = dict(attrs_list)
        if tag == "form":
            if attrs.get("id") == self.form_id:
                self._in_form = True
                self.action = attrs.get("action")
                self.method = (attrs.get("method") or "get").lower()
        elif tag == "input":
            if self._owned(attrs) and attrs.get("name"):
                self.controls.append(
                    {
                        "type": attrs.get("type", "text"),
                        "name": attrs["name"],
                        "value": attrs.get("value", ""),
                        "checked": "checked" in attrs,
                    }
                )
        elif tag == "select":
            if self._owned(attrs) and attrs.get("name"):
                self._select = {
                    "type": "select",
                    "name": attrs["name"],
                    "value": None,
                    "first": None,
                }
        elif tag == "option" and self._select is not None:
            value = attrs.get("value", "")
            if self._select["first"] is None:
                self._select["first"] = value
            if "selected" in attrs:
                self._select["value"] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False
        elif tag == "select" and self._select is not None:
            sel = self._select
            self._select = None
            self.controls.append(
                {
                    "type": "select",
                    "name": sel["name"],
                    "value": sel["value"] if sel["value"] is not None else sel["first"] or "",
                    "checked": False,
                }
            )


def _serialise(controls: list[dict], tick: dict[tuple[str, str], bool]) -> list[tuple[str, str]]:
    """The browser's form-data-set construction: tree order; hidden inputs and
    selects always successful; a checkbox only when checked. ``tick`` overrides
    checkbox state, keyed by ``(name, value)`` — the two toggles as
    ``("thinking"/"attachments", "1")``, row selection as
    ``("conversation_uuid", <uuid>)``."""
    pairs: list[tuple[str, str]] = []
    for c in controls:
        if c["type"] == "checkbox":
            if tick.get((c["name"], c["value"]), c["checked"]):
                pairs.append((c["name"], c["value"]))
        elif c["type"] in ("hidden", "text", "select"):
            pairs.append((c["name"], c["value"]))
    return pairs


def _detail_form(client, uuid: str, thinking: bool, attachments: bool):
    """Action URL and browser-serialised pairs for the REAL detail-page form."""
    page = client.get(f"/conversations/{uuid}").get_data(as_text=True)
    fc = _FormControls("export-form")
    fc.feed(page)
    assert fc.action == f"/conversations/{uuid}/export"
    assert fc.method == "get"
    pairs = _serialise(
        fc.controls,
        {("thinking", "1"): thinking, ("attachments", "1"): attachments},
    )
    return fc.action, pairs


def _as_form_data(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Pairs → dict-of-lists for the test client, preserving per-name order
    (the property the endpoint's first-value read depends on)."""
    data: dict[str, list[str]] = {}
    for name, value in pairs:
        data.setdefault(name, []).append(value)
    return data


# ---------------------------------------------------------------------------
# B3 — four checkbox states through the real form: Export path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("thinking", "attachments"), STATES, ids=STATE_IDS)
def test_export_form_thinking_state_honoured(client, thinking, attachments) -> None:
    action, pairs = _detail_form(client, CONV_THINKING, thinking, attachments)
    doc = client.get(f"{action}?{urlencode(pairs)}").get_data(as_text=True)
    assert ("[Thinking]" in doc) is thinking
    assert (f"thinking: {'included' if thinking else 'omitted'}") in doc


@pytest.mark.parametrize(("thinking", "attachments"), STATES, ids=STATE_IDS)
def test_export_form_attachment_state_honoured(client, thinking, attachments) -> None:
    action, pairs = _detail_form(client, CONV_ATTACHMENT, thinking, attachments)
    doc = client.get(f"{action}?{urlencode(pairs)}").get_data(as_text=True)
    if attachments:
        assert "attachments: included" in doc
        assert "text not exported" not in doc
    else:
        assert "attachments: referenced" in doc
        assert "— text not exported." in doc


# ---------------------------------------------------------------------------
# B2 — four checkbox states through the real form: Copy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("thinking", "attachments"), STATES, ids=STATE_IDS)
def test_copy_path_states_honoured_without_frontmatter(client, thinking, attachments) -> None:
    """conv_actions.js serialises the same form with URLSearchParams(FormData)
    and appends frontmatter=0 — replicated here pair-for-pair. The copied text
    must honour both toggles and drop the YAML header (H1 stays)."""
    for uuid in (CONV_THINKING, CONV_ATTACHMENT):
        action, pairs = _detail_form(client, uuid, thinking, attachments)
        query = urlencode([*pairs, ("frontmatter", "0")])
        doc = client.get(f"{action}?{query}").get_data(as_text=True)
        assert not doc.startswith("---")
        assert doc.startswith("# ")
        if uuid == CONV_THINKING:
            assert ("[Thinking]" in doc) is thinking
        else:
            assert ("text not exported" in doc) is (not attachments)


# ---------------------------------------------------------------------------
# B3 — four checkbox states through the real bulk form (conversations list)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("thinking", "attachments"), STATES, ids=STATE_IDS)
def test_bulk_form_states_honoured(client, thinking, attachments) -> None:
    page = client.get("/conversations").get_data(as_text=True)
    # The Export selected button redirects the POST via formaction.
    assert 'formaction="/conversations/export"' in page
    fc = _FormControls("bulk-form")
    fc.feed(page)
    pairs = _serialise(
        fc.controls,
        {
            ("thinking", "1"): thinking,
            ("attachments", "1"): attachments,
            ("conversation_uuid", CONV_THINKING): True,
            ("conversation_uuid", CONV_ATTACHMENT): True,
        },
    )
    r = client.post("/conversations/export", data=_as_form_data(pairs))
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
        docs = [zf.read(n).decode("utf-8") for n in zf.namelist()]
    assert len(docs) == 2
    thinking_doc = next(d for d in docs if f"uuid: {CONV_THINKING}" in d)
    attachment_doc = next(d for d in docs if f"uuid: {CONV_ATTACHMENT}" in d)
    assert ("[Thinking]" in thinking_doc) is thinking
    assert (f"thinking: {'included' if thinking else 'omitted'}") in thinking_doc
    assert (f"attachments: {'included' if attachments else 'referenced'}") in attachment_doc
    assert ("text not exported" in attachment_doc) is (not attachments)


# ---------------------------------------------------------------------------
# B2 — control grouping: fieldset, byte-exact legend, Print outside the group
# ---------------------------------------------------------------------------


def test_export_controls_grouped_in_fieldset_with_print_outside(client) -> None:
    page = client.get(f"/conversations/{CONV_MINIMAL}").get_data(as_text=True)
    fs = page.index('<fieldset class="export-options">')
    fs_end = page.index("</fieldset>", fs)
    assert page.index("<legend>Export options</legend>", fs) < fs_end

    # The two toggles and the two actions they govern sit inside the group.
    for marker in (
        'name="thinking"',
        'name="attachments"',
        ">Export</button>",
        "Copy conversation",
        ">Copied</span>",
    ):
        assert fs < page.index(marker, fs) < fs_end, marker

    # Print / Save as PDF is screen-WYSIWYG — outside the group AND outside
    # the form, so no toggle can appear to govern it.
    form_end = page.index("</form>", fs)
    print_pos = page.index("Print / Save as PDF")
    assert print_pos > fs_end
    assert print_pos > form_end
