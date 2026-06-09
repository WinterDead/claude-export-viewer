"""Display-layer sender labels (v1.1 Session A, item A1).

The parser's raw ``sender`` values ("human"/"assistant") are unchanged; only
the user-facing label is mapped, via the ``sender_label`` Jinja filter:
"human" → "User", "assistant" → "Assistant", unknown values pass through.

The negative assertions target the label *elements* (by class/structure),
never the whole page body — message content may legitimately contain the
word "human", and the synthetic fixture's "hello from human" message is used
below as exactly that control.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import create_app
from viewer.render import sender_label

# Conversations in tests/fixtures/synthetic_export.zip.
CONV_MINIMAL = "00000000-0000-0000-0000-0000000000c1"
CONV_BRANCHED = "00000000-0000-0000-0000-0000000000f1"

# The sender-label elements, one pattern per surface.
MSG_SENDER_RE = re.compile(r'<span class="msg-sender">([^<]*)</span>')
SEARCH_SENDER_RE = re.compile(r'<span class="search-message-sender">([^<]*)</span>')
BRANCH_OPTION_RE = re.compile(r'class="branch-option[^"]*"[^>]*>\s*\d+\.\s*([A-Za-z]+)')


@pytest.fixture
def synthetic_client(tmp_path: Path, fixtures_dir: Path):
    app = create_app(
        fixtures_dir / "synthetic_export.zip",
        db_path=tmp_path / "viewer.sqlite",
    )
    return app.test_client()


# ---------------------------------------------------------------------------
# Filter unit behaviour
# ---------------------------------------------------------------------------


def test_sender_label_maps_known_senders() -> None:
    assert sender_label("human") == "User"
    assert sender_label("assistant") == "Assistant"


def test_sender_label_passes_unknown_senders_through() -> None:
    # Preserve-and-flag: the parser warned at parse time; the renderer
    # surfaces the raw value rather than guessing a label.
    assert sender_label("tool") == "tool"


def test_sender_label_empty_input() -> None:
    assert sender_label("") == ""
    assert sender_label(None) == ""


# ---------------------------------------------------------------------------
# Conversation detail — byline labels
# ---------------------------------------------------------------------------


def test_detail_page_shows_user_label_on_human_message(synthetic_client) -> None:
    body = synthetic_client.get(f"/conversations/{CONV_MINIMAL}").get_data(as_text=True)
    labels = MSG_SENDER_RE.findall(body)
    assert labels, "expected at least one sender-label element"
    assert "User" in [lab.strip() for lab in labels]
    assert "Assistant" in [lab.strip() for lab in labels]


def test_detail_page_sender_label_never_says_human(synthetic_client) -> None:
    # The minimal conversation's first message body is "hello from human" —
    # the *content* may say "human"; the label element must not.
    body = synthetic_client.get(f"/conversations/{CONV_MINIMAL}").get_data(as_text=True)
    assert "hello from human" in body  # control: content keeps the word
    for label in MSG_SENDER_RE.findall(body):
        assert "human" not in label.lower()


# ---------------------------------------------------------------------------
# Branch switcher — sibling rows carry sender labels too
# ---------------------------------------------------------------------------


def test_branch_switcher_options_use_display_labels(synthetic_client) -> None:
    body = synthetic_client.get(f"/conversations/{CONV_BRANCHED}").get_data(as_text=True)
    options = BRANCH_OPTION_RE.findall(body)
    assert options, "expected branch-switcher options on the branched conversation"
    for word in options:
        assert word.lower() != "human"
        assert word in ("User", "Assistant")


# ---------------------------------------------------------------------------
# Search results — message-hit labels
# ---------------------------------------------------------------------------


def test_search_hit_sender_label_is_user(synthetic_client) -> None:
    body = synthetic_client.get('/search?q="hello from human"').get_data(as_text=True)
    labels = SEARCH_SENDER_RE.findall(body)
    assert labels, "expected a message hit with a sender label"
    for label in labels:
        assert "human" not in label.lower()
    assert "User" in [lab.strip() for lab in labels]
