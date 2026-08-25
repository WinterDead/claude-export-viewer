"""Secret-key handling (v1.1.5 Session J, Task C).

``create_app`` establishes a fresh ``SECRET_KEY`` on every start via
``secrets.token_hex(32)`` — no committed literal, no environment fallback.
The consequence (CSRF tokens don't survive a restart) is accepted for a
localhost single-user tool; these tests pin the mechanism so a future
refactor can't quietly downgrade it to a hardcoded key.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from app import create_app


def _fresh_app(export_path: Path, tmp_path: Path, name: str) -> Flask:
    return create_app(
        export_path,
        db_path=tmp_path / f"{name}.sqlite",
    )


def test_secret_key_is_fresh_per_start(export_path: Path, tmp_path: Path) -> None:
    first = _fresh_app(export_path, tmp_path, "first").config["SECRET_KEY"]
    second = _fresh_app(export_path, tmp_path, "second").config["SECRET_KEY"]
    assert first != second


def test_secret_key_is_256_bit_hex(export_path: Path, tmp_path: Path) -> None:
    key = _fresh_app(export_path, tmp_path, "strength").config["SECRET_KEY"]
    assert isinstance(key, str)
    assert len(key) == 64  # token_hex(32): 32 random bytes, hex-encoded
    int(key, 16)  # raises if anything non-hex slipped in
