"""Launcher scripts (v1.1 Session E; hardening v1.1.5 Session J).

Shell scripts don't unit-test well in this project's pytest setup, so the
heavy verification is the manual matrix recorded in
``docs/briefs/v1.1-session-e-verification.md``. These tests cover what is
cheap to automate: the three files exist, the ``.bat`` shim actually points
at ``run.ps1`` with a per-invocation policy bypass, and the scripts honour
the public-trajectory rule (no drive-letter paths, no machine usernames —
same verify-negatives spirit as ``tests/test_help.py``).

Session J adds content guards for the venv hardening (missing-venv-module
message, pip probe, rebuild notice) and the explicit 127.0.0.1 host binding,
plus a canary on the Flask CLI's default host — the README's and help page's
manual commands omit ``--host`` on purpose and rely on that default.
"""

from __future__ import annotations

import getpass
import json
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


# ---------------------------------------------------------------------------
# Venv hardening (v1.1.5 Session J, Task A)
# ---------------------------------------------------------------------------

REBUILD_NOTICE = "The app's virtual environment is incomplete — rebuilding it..."


@pytest.mark.parametrize("name", ("run.sh", "run.ps1"))
def test_launcher_probes_existing_venv_for_pip(name: str) -> None:
    # A half-created .venv (python present, pip missing) must be detected
    # before the install step trusts it.
    body = (PROJECT_ROOT / name).read_text(encoding="utf-8")
    assert "-m pip --version" in body


@pytest.mark.parametrize("name", ("run.sh", "run.ps1"))
def test_launcher_carries_rebuild_notice(name: str) -> None:
    body = (PROJECT_ROOT / name).read_text(encoding="utf-8")
    assert REBUILD_NOTICE in body


def test_run_sh_explains_missing_venv_module() -> None:
    # Debian-family systems ship Python without the venv module; the failure
    # message must name the fix rather than surface a raw ensurepip error.
    body = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")
    assert "can't create virtual environments yet" in body
    assert "sudo apt update && sudo apt install python3-venv" in body


# ---------------------------------------------------------------------------
# Explicit host binding (v1.1.5 Session J, Task B)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ("run.sh", "run.ps1"))
def test_launcher_flask_run_binds_loopback_explicitly(name: str) -> None:
    # Every flask-run command line in the launchers — real invocations and
    # printed manual instructions alike — states the loopback bind.
    body = (PROJECT_ROOT / name).read_text(encoding="utf-8")
    lines = [line for line in body.splitlines() if "flask --app app run" in line]
    assert lines, f"no flask run command found in {name}"
    for line in lines:
        assert "--host 127.0.0.1" in line, line


def test_launch_json_binds_loopback_explicitly() -> None:
    config = json.loads((PROJECT_ROOT / ".claude" / "launch.json").read_text(encoding="utf-8"))
    args = config["configurations"][0]["runtimeArgs"]
    assert args[args.index("--host") + 1] == "127.0.0.1"


def test_flask_cli_run_default_host_is_loopback() -> None:
    # Canary for the implicit bindings this repo deliberately leaves alone
    # (the manual commands in README.md and help.md): fails if the installed
    # Flask CLI's ``run`` default host ever stops being loopback.
    from flask.cli import run_command

    host = next(p for p in run_command.params if p.name == "host")
    assert host.default == "127.0.0.1"
