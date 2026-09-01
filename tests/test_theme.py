"""Dark mode (v1.3.0 Session T).

Covers the session's ratified design:

- Theme class resolution on ``<html>`` from the ``viewer_theme`` cookie —
  absence, each valid value, and a mangled value all resolve.
- The nav Theme control: renders in the nav tail after Help with the C4
  copy, posts through its real rendered form (the Session O meta-gap rule),
  sets the cookie for a year, and bounces back to the submitting page.
- The CSS theme contract: the two dark token blocks (explicit ``.theme-dark``
  and ``.theme-system`` under ``prefers-color-scheme: dark``) stay identical,
  and the ``@media print`` reset restores every dark-varying token to its
  light (``:root``) value — print output is theme-independent.

The theme-colour metas and the highlight.js stylesheet matrix are pinned in
``tests/test_highlighting.py`` alongside the other asset pins.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from tests.conftest import scrape_csrf
from viewer.routes import THEME_COOKIE, THEME_COOKIE_MAX_AGE, THEMES

STYLE_CSS = Path(__file__).resolve().parents[1] / "static" / "style.css"


# ---------------------------------------------------------------------------
# Theme class resolution from the cookie
# ---------------------------------------------------------------------------


def _html_class(body: str) -> str:
    m = re.search(r'<html[^>]*class="([^"]+)"', body)
    assert m, "no class on <html>"
    return m.group(1)


def test_theme_defaults_to_system_without_cookie(client) -> None:
    assert _html_class(client.get("/").get_data(as_text=True)) == "theme-system"


def test_theme_class_follows_cookie(client) -> None:
    for theme in THEMES:
        client.set_cookie(THEME_COOKIE, theme)
        assert _html_class(client.get("/").get_data(as_text=True)) == f"theme-{theme}"


def test_mangled_cookie_resolves_to_system(client) -> None:
    client.set_cookie(THEME_COOKIE, "neon")
    assert _html_class(client.get("/").get_data(as_text=True)) == "theme-system"


# ---------------------------------------------------------------------------
# The control: C4 copy, placement, and the real rendered form
# ---------------------------------------------------------------------------


class _ThemeForm(HTMLParser):
    """The nav theme control as rendered: action, method, hidden fields, the
    select's name, and its option values/labels."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.method: str | None = None
        self.hidden: dict[str, str] = {}
        self.select_name: str | None = None
        self.options: list[tuple[str, str]] = []
        self._in_form = False
        self._optvalue: str | None = None

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = dict(attrs_list)
        if tag == "form" and "theme-form" in (attrs.get("class") or ""):
            self._in_form = True
            self.action = attrs.get("action")
            self.method = (attrs.get("method") or "get").lower()
        elif self._in_form and tag == "input" and attrs.get("type") == "hidden":
            self.hidden[attrs.get("name", "")] = attrs.get("value", "")
        elif self._in_form and tag == "select":
            self.select_name = attrs.get("name")
        elif self._in_form and tag == "option":
            self._optvalue = attrs.get("value", "")

    def handle_data(self, data: str) -> None:
        if self._optvalue is not None and data.strip():
            self.options.append((self._optvalue, data.strip()))
            self._optvalue = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False


def _scrape_theme_form(client, page: str = "/") -> _ThemeForm:
    body = client.get(page).get_data(as_text=True)
    form = _ThemeForm()
    form.feed(body)
    assert form.action, f"no theme form on {page}"
    return form


def test_control_renders_after_help_with_c4_copy(client) -> None:
    body = client.get("/").get_data(as_text=True)
    # Copy C4: label Theme; options System, Light, Dark.
    assert '<span class="theme-form-text">Theme</span>' in body
    form = _scrape_theme_form(client)
    assert form.method == "post"
    assert form.select_name == "theme"
    assert form.options == [("system", "System"), ("light", "Light"), ("dark", "Dark")]
    # Nav tail: the control sits after the Help link.
    assert body.index('class="theme-control"') > body.index(">Help</a>")


def test_choice_posts_through_real_form_and_sets_cookie(client) -> None:
    form = _scrape_theme_form(client, "/help")
    assert form.hidden["next"] == "/help"
    r = client.post(
        form.action,
        data={
            "csrf_token": scrape_csrf(client, "/help"),
            "next": form.hidden["next"],
            form.select_name: "dark",
        },
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/help")
    cookie = r.headers.get("Set-Cookie", "")
    assert f"{THEME_COOKIE}=dark" in cookie
    assert f"Max-Age={THEME_COOKIE_MAX_AGE}" in cookie
    assert "SameSite=Lax" in cookie
    # The very next render carries the choice — no flash of the wrong theme.
    assert _html_class(client.get("/help").get_data(as_text=True)) == "theme-dark"


def test_unknown_theme_value_is_refused(client) -> None:
    r = client.post(
        "/theme",
        data={"csrf_token": scrape_csrf(client, "/"), "theme": "neon", "next": "/"},
    )
    assert r.status_code == 400


def test_offsite_next_bounces_to_home(client) -> None:
    r = client.post(
        "/theme",
        data={
            "csrf_token": scrape_csrf(client, "/"),
            "theme": "light",
            "next": "https://example.invalid/elsewhere",
        },
    )
    assert r.status_code == 302
    assert r.headers["Location"] in ("/", "http://localhost/")


# ---------------------------------------------------------------------------
# CSS contract: identical dark blocks; print resets every dark-varying token
# ---------------------------------------------------------------------------


def _custom_props(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))


def _css_parts() -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str], str]:
    css = STYLE_CSS.read_text(encoding="utf-8")

    root = _custom_props(re.search(r"(?m)^:root\s*\{([^}]*)\}", css).group(1))

    dark: dict[str, str] = {}
    for block in re.findall(r"(?m)^html\.theme-dark\s*\{([^}]*)\}", css):
        dark.update(_custom_props(block))

    media = re.search(
        r"@media \(prefers-color-scheme: dark\)\s*\{\s*html\.theme-system\s*\{([^}]*)\}",
        css,
    )
    assert media, "no .theme-system dark block under prefers-color-scheme"
    system_dark = _custom_props(media.group(1))

    print_block = css[css.index("@media print") :]
    reset = re.search(
        r"html\.theme-light,\s*html\.theme-system,\s*html\.theme-dark\s*\{([^}]*)\}",
        print_block,
    )
    assert reset, "no theme reset inside @media print"
    print_reset = _custom_props(reset.group(1))

    return root, dark, system_dark, print_reset, css


def test_dark_token_blocks_are_identical() -> None:
    _root, dark, system_dark, _reset, _css = _css_parts()
    assert len(dark) >= 20, "dark token set suspiciously small"
    assert dark == system_dark


def test_print_reset_covers_every_dark_varying_token() -> None:
    root, dark, _system_dark, print_reset, _css = _css_parts()
    for name in dark:
        assert name in root, f"{name} has no light default in :root"
        assert name in print_reset, f"{name} not reset for print"
        assert print_reset[name] == root[name], f"{name} print value differs from light"


def test_every_var_reference_has_a_root_definition() -> None:
    root, _dark, _system_dark, _reset, css = _css_parts()
    refs = set(re.findall(r"var\((--[\w-]+)\)", css))
    assert refs <= set(root), f"dangling tokens: {sorted(refs - set(root))}"
