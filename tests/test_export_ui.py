"""Export UI, print polish, and copy fixes (v1.2 Session N).

Template-level pins for the two export controls (checkbox defaults, byte-exact
labels, branch WYSIWYG), the ``frontmatter=0`` endpoint behaviour, the
inspect-mode action bar, content guards on the print stylesheet and the two
new JS files, and the branch-label pluralisation surface.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from app import create_app
from viewer.export import serialise_conversation
from viewer.parser import load_export

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

CONV_MINIMAL = "00000000-0000-0000-0000-0000000000c1"
CONV_BRANCHING = "00000000-0000-0000-0000-0000000000f1"
BRANCH_B = "00000000-0000-0000-0000-0000000000f4"


@pytest.fixture
def client(fixtures_dir: Path, tmp_path: Path):
    app = create_app(
        fixtures_dir / "synthetic_export.zip",
        db_path=tmp_path / "viewer.sqlite",
    )
    return app.test_client()


def _detail_page(client, uuid: str = CONV_MINIMAL, query: str = "") -> str:
    r = client.get(f"/conversations/{uuid}{query}")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _checkbox_tag(page: str, name: str) -> str:
    """The full ``<input type="checkbox" ...>`` tag for ``name``."""
    m = re.search(rf'<input type="checkbox" name="{name}"[^>]*>', page)
    assert m, f"no {name} checkbox on the page"
    return m.group(0)


# ---------------------------------------------------------------------------
# A1 — detail-page Export control
# ---------------------------------------------------------------------------


def test_detail_export_form_targets_get_endpoint(client) -> None:
    page = _detail_page(client)
    assert f'action="/conversations/{CONV_MINIMAL}/export"' in page
    assert 'method="get"' in page


def test_detail_checkbox_defaults_pinned_at_template_level(client) -> None:
    page = _detail_page(client)
    assert "checked" not in _checkbox_tag(page, "thinking")
    assert "checked" in _checkbox_tag(page, "attachments")


def test_detail_checkbox_labels_byte_exact(client) -> None:
    page = _detail_page(client)
    assert "Include thinking blocks" in page
    assert "Include attachment text" in page


def test_detail_buttons_present_with_authored_labels(client) -> None:
    page = _detail_page(client)
    assert re.search(r'<button type="submit" class="export-btn">\s*Export\s*</button>', page)
    assert re.search(r">\s*Copy conversation\s*</button>", page)
    assert re.search(r">\s*Copied\s*</span>", page)
    assert re.search(r">\s*Print / Save as PDF\s*</button>", page)


def test_detail_checkbox_precedes_its_hidden_zero_fallback(client) -> None:
    """The endpoint reads the FIRST submitted value per name, so the ticked
    checkbox's 1 must precede the hidden 0 in document order."""
    page = _detail_page(client)
    for name in ("thinking", "attachments"):
        cb = page.index(f'<input type="checkbox" name="{name}"')
        hidden = page.index(f'<input type="hidden" name="{name}" value="0">')
        assert cb < hidden


def test_detail_export_form_reemits_branch_parameters(client) -> None:
    """WYSIWYG: the form carries the page's current branch combination."""
    page = _detail_page(client, CONV_BRANCHING, f"?branch={BRANCH_B}")
    assert f'<input type="hidden" name="branch" value="{BRANCH_B}">' in page
    # Without an override there is nothing to re-emit.
    page = _detail_page(client, CONV_BRANCHING)
    assert 'name="branch"' not in page


# ---------------------------------------------------------------------------
# A3 — frontmatter=0 (the copy control's fetch form)
# ---------------------------------------------------------------------------


def test_frontmatter_0_drops_header_keeps_h1(client) -> None:
    r = client.get(f"/conversations/{CONV_MINIMAL}/export?frontmatter=0")
    assert r.status_code == 200
    doc = r.get_data(as_text=True)
    assert not doc.startswith("---")
    assert "generator:" not in doc
    assert doc.startswith("# Synthetic minimal conversation\n")


def test_frontmatter_present_by_default(client) -> None:
    doc = client.get(f"/conversations/{CONV_MINIMAL}/export").get_data(as_text=True)
    assert doc.startswith("---\n")


def test_serialiser_frontmatter_default_is_on(fixtures_dir: Path) -> None:
    export = load_export(fixtures_dir / "synthetic_export.zip")
    conv = next(c for c in export.conversations if c.uuid == CONV_MINIMAL)
    assert serialise_conversation(conv, conv.canonical_path).startswith("---\n")
    no_fm = serialise_conversation(conv, conv.canonical_path, include_frontmatter=False)
    assert no_fm.startswith("# ")


def test_copy_button_js_wiring(client) -> None:
    js = (STATIC_DIR / "conv_actions.js").read_text(encoding="utf-8")
    assert 'append("frontmatter", "0")' in js
    assert "navigator.clipboard.writeText" in js
    assert "window.print()" in js
    page = _detail_page(client)
    assert "conv_actions.js" in page


# ---------------------------------------------------------------------------
# A2 — conversations-list action bar (both modes)
# ---------------------------------------------------------------------------


def _list_page(client) -> str:
    r = client.get("/conversations")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_list_bar_carries_export_action_with_defaults(client) -> None:
    page = _list_page(client)
    assert 'formaction="/conversations/export"' in page
    assert "Export selected" in page
    assert "checked" not in _checkbox_tag(page, "thinking")
    assert "checked" in _checkbox_tag(page, "attachments")
    # Tagging actions still present in normal mode.
    assert "Tag selected" in page
    assert "Untag selected" in page


def _start_inspection(client, fixtures_dir: Path) -> None:
    body = client.get("/inspect").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
    zip_bytes = (fixtures_dir / "synthetic_export.zip").read_bytes()
    r = client.post(
        "/inspect",
        data={"csrf_token": token, "export_zip": (io.BytesIO(zip_bytes), "other.zip")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 302


def test_inspect_mode_bar_carries_only_export(client, fixtures_dir: Path) -> None:
    """The action bar APPEARS in inspect mode (exporting from an inspected
    archive is a primary use case) carrying only the export action — every
    tagging control stays structurally absent."""
    _start_inspection(client, fixtures_dir)
    page = _list_page(client)
    assert 'id="bulk-form"' in page
    assert "Export selected" in page
    assert 'class="conv-checkbox"' in page  # row checkboxes present too
    # The bar posts to the export endpoint and offers no tagging action.
    assert 'action="/conversations/export"' in page
    assert "Tag selected" not in page
    assert "Retag all" not in page
    assert "Untag selected" not in page
    assert "bulk-project-picker" not in page
    assert "tag-filter" not in page  # Session K isolation unchanged


def test_bulk_export_from_inspect_mode_bar_posts_successfully(client, fixtures_dir: Path) -> None:
    _start_inspection(client, fixtures_dir)
    body = client.get("/conversations").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
    r = client.post(
        "/conversations/export",
        data={"csrf_token": token, "conversation_uuid": [CONV_MINIMAL]},
    )
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
        assert len(zf.namelist()) == 1


def test_checkbox_hidden_pair_semantics_at_bulk_endpoint(client) -> None:
    """First-value-wins: a ticked box submits 1 then its 0 fallback; an
    unticked box submits the 0 alone — the browser-form encoding the bar's
    checkbox/hidden pairs produce."""
    body = client.get("/conversations").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)

    def _bulk(attachments: list[str]) -> str:
        r = client.post(
            "/conversations/export",
            data={
                "csrf_token": token,
                "conversation_uuid": ["00000000-0000-0000-0000-0000000000e1"],
                "attachments": attachments,
            },
        )
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
            return zf.read(zf.namelist()[0]).decode("utf-8")

    assert "attachments: included" in _bulk(["1", "0"])  # ticked
    assert "attachments: referenced" in _bulk(["0"])  # unticked


# ---------------------------------------------------------------------------
# B — print-CSS content guards
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def print_css() -> str:
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    return css[css.index("@media print {") :]


def test_print_css_details_rules_deleted(print_css: str) -> None:
    """B1 — deliberate WYSIWYG: both old <details> print rules gone, and no
    force-open via the modern pseudo-element anywhere (do-not-resurrect).
    Comments are stripped first — the do-not-resurrect note itself quotes
    the forbidden selector."""
    print_rules = re.sub(r"/\*.*?\*/", "", print_css, flags=re.DOTALL)
    assert "details > summary" not in print_rules
    assert "details > *:not(summary)" not in print_rules
    full_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    rules_only = re.sub(r"/\*.*?\*/", "", full_css, flags=re.DOTALL)
    assert "::details-content" not in rules_only


def test_print_css_hides_memory_block_controls(print_css: str) -> None:
    hide_list = print_css[: print_css.index("display: none !important;")]
    assert ".memory-block-controls" in hide_list


def test_print_css_hides_message_timestamps(print_css: str) -> None:
    assert ".msg-header time" in print_css


def test_print_css_wraps_code(print_css: str) -> None:
    assert "white-space: pre-wrap;" in print_css
    assert "overflow-x: visible;" in print_css


def test_print_css_section_break_rule_pruned(print_css: str) -> None:
    assert not re.search(r"(?<![\w.\-])section\s*\{", print_css)


def test_print_css_branch_switcher_prints_label_only(print_css: str) -> None:
    hide_list = print_css[: print_css.index("display: none !important;")]
    assert ".branch-switcher" not in hide_list  # no longer hidden wholesale
    assert ".branch-options" in print_css  # the option list is
    assert ".branch-label-colon" in print_css  # and the screen colon


def test_print_only_classes_hidden_on_screen_shown_in_print(print_css: str) -> None:
    full_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    screen_css = full_css[: full_css.index("@media print {")]
    assert re.search(r"\.print-link-marker,\s*\.print-links\s*\{\s*display: none;", screen_css)
    assert ".print-link-marker" in print_css
    assert ".print-links" in print_css


# ---------------------------------------------------------------------------
# B4 — links-appendix JS content guards
# ---------------------------------------------------------------------------


def test_print_links_js_present_and_loaded(client) -> None:
    js = (STATIC_DIR / "print_links.js").read_text(encoding="utf-8")
    assert "print-link-marker" in js
    assert "print-links" in js
    assert ".markdown-body a[href]" in js  # content scope, not viewer chrome
    assert "numberByUrl" in js  # repeated URLs share one number
    assert "beforeprint" in js  # open/closed state honoured at print time
    assert "Links" in js
    assert "conv_actions.js" in _detail_page(client)
    assert "print_links.js" in _detail_page(client)


# ---------------------------------------------------------------------------
# C2 — branch-label pluralisation (the print/screen surface)
# ---------------------------------------------------------------------------


def test_branch_label_pluralises(client) -> None:
    page = _detail_page(client, CONV_BRANCHING)
    assert re.search(r"Branch point — 2 sibling\s+responses", page)


def test_branch_label_singular_form_exists_in_template() -> None:
    """The singular can't render on screen (a switcher needs two siblings),
    so the capability is pinned at the template: the plural s is conditional
    on the count."""
    tpl = (TEMPLATES_DIR / "conversation_detail.html").read_text(encoding="utf-8")
    assert re.search(r"response\{%\s*if siblings \| length != 1\s*%\}s\{%\s*endif\s*%\}", tpl)
