#!/usr/bin/env bash
# Claude Export Viewer launcher (macOS / Linux).
#
# This script never changes anything outside the project folder: no PATH
# edits, no installs beyond the local .venv.

set -u
cd "$(dirname "$0")" || exit 1

MIN_MAJOR=3
MIN_MINOR=11
PORTS="5000 5050 5051 5052"
DEFAULT_PORT=5000

version_of() {
    "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null
}

version_ok() {
    "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" \
        2>/dev/null
}

# --- 1. Find a suitable Python -------------------------------------------

PYTHON=""
NOTES=""
for candidate in python3 python; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
        NOTES="${NOTES}  - ${candidate}: not found
"
        continue
    fi
    version="$(version_of "$candidate")"
    if [ -z "$version" ]; then
        NOTES="${NOTES}  - ${candidate}: found but it did not run
"
        continue
    fi
    if version_ok "$candidate"; then
        PYTHON="$candidate"
        break
    fi
    NOTES="${NOTES}  - ${candidate}: Python ${version} - too old
"
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "Could not find Python ${MIN_MAJOR}.${MIN_MINOR} or newer on this machine."
    echo ""
    echo "What was checked:"
    printf '%s' "$NOTES"
    echo ""
    echo "Next step: install Python 3.11 or newer from"
    echo "  https://www.python.org/downloads/"
    echo "(or from your system's package manager), then run this script again."
    exit 1
fi

echo "Found Python $(version_of "$PYTHON") (${PYTHON})."

# --- 2. Create the virtual environment if absent --------------------------

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
    echo "Creating the project's virtual environment (.venv)..."
    if ! "$PYTHON" -m venv .venv || [ ! -x "$VENV_PY" ]; then
        echo "Creating .venv did not work."
        echo "Next step: delete the .venv folder if one was half-created, then run this script again."
        exit 1
    fi
fi

# Install the app into the venv only when its dependencies are not importable
# yet. Cheap when already installed, and self-heals a half-finished first run.
if ! "$VENV_PY" -c 'import flask, flask_wtf, markdown, bleach' >/dev/null 2>&1; then
    echo "Installing the app and its dependencies (this can take a minute)..."
    if ! "$VENV_PY" -m pip install -e .; then
        echo "Installing dependencies did not work."
        echo "Next step: check your internet connection, then run this script again."
        exit 1
    fi
fi

# --- 3. Pick a port --------------------------------------------------------

port_free() {
    "$VENV_PY" - "$1" <<'PYEOF'
import socket
import sys

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PYEOF
}

PORT=""
for candidate in $PORTS; do
    if port_free "$candidate"; then
        PORT="$candidate"
        break
    fi
    echo "Port $candidate is busy, trying the next one..."
done

if [ -z "$PORT" ]; then
    echo "All of the usual ports ($PORTS) are busy."
    echo "Next step: close whatever is using them, or start the app yourself on a port of your choice:"
    echo "  .venv/bin/python -m flask --app app run --port 8080"
    exit 1
fi

if [ "$PORT" != "$DEFAULT_PORT" ]; then
    echo "Using port $PORT instead of the default $DEFAULT_PORT."
fi

# --- 4. Launch, and open the browser once the server answers ---------------

URL="http://127.0.0.1:${PORT}/"

(
    # Poll until the server accepts a connection, then open the default
    # browser. Gives up quietly after 30 seconds.
    "$VENV_PY" - "$PORT" <<'PYEOF'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 30
while time.time() < deadline:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.25)
        s.close()
        sys.exit(0)
    except OSError:
        time.sleep(0.25)
sys.exit(1)
PYEOF
    if [ $? -eq 0 ]; then
        if command -v open >/dev/null 2>&1; then
            open "$URL"
        elif command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$URL" >/dev/null 2>&1
        else
            echo "Open $URL in your browser."
        fi
    fi
) &

echo ""
echo "Starting Claude Export Viewer at $URL"
echo "Your browser should open by itself. Press Ctrl+C in this window to stop the app."
echo ""

exec "$VENV_PY" -m flask --app app run --port "$PORT"
