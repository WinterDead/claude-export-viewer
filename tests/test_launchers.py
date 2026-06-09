"""Launcher scripts (v1.1 Session E).

Shell scripts don't unit-test well in this project's pytest setup, so the
heavy verification is the manual matrix recorded in
``docs/briefs/v1.1-session-e-verification.md``. These tests cover what is
cheap to automate: the three files exist, the ``.bat`` shim actually points
at ``run.ps1`` with a per-invocation policy bypass, and the scripts honour
the public-trajectory rule (no drive-letter paths, no machine usernames —
same verify-negatives spirit as ``tests/test_help.py``).
"""

from __future__ import annotations

import getpass
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHERS = ("run.ps1", "run.bat", "run.sh")


@pytest.mark.parametrize("name", LAUNCHERS)
def test_launcher_exists(name: str) -> None:
    assert (PROJECT_ROOT / name).is_file()


def test_bat_shim_invokes_ps1() -> None:
    body = (PROJECT_ROOT / "run.bat").read_text(encoding="utf-8")
    assert "run.ps1" in body
    assert "-ExecutionPolicy Bypass" in body


@pytest.mark.parametrize("name", LAUNCHERS)
def test_no_drive_letter_paths(name: str) -> None:
    body = (PROJECT_ROOT / name).read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:\\", body)


@pytest.mark.parametrize("name", LAUNCHERS)
def test_no_machine_username(name: str) -> None:
    # Checked dynamically so nothing personal is committed in this file, and
    # the guard still works on any contributor's machine.
    username = getpass.getuser()
    if len(username) >= 4:  # too-short names false-positive on ordinary words
        body = (PROJECT_ROOT / name).read_text(encoding="utf-8").lower()
        assert username.lower() not in body


def test_run_sh_is_bash_with_lf_endings() -> None:
    # CRLF in run.sh breaks bash on macOS/Linux; .gitattributes pins it to LF
    # and this guards the working copy on a Windows dev machine.
    raw = (PROJECT_ROOT / "run.sh").read_bytes()
    assert raw.startswith(b"#!/usr/bin/env bash")
    assert b"\r" not in raw
