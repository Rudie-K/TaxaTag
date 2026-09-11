#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Starts TaxaTag on macOS and Linux.
#
# On first run this creates a private virtual environment inside the TaxaTag
# folder and installs what it needs, so nothing on the rest of the system is
# touched. Later runs reuse it and start immediately.
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

case "$(uname -s)" in
    Darwin) PLATFORM_DIR="macos" ;;
    Linux)  PLATFORM_DIR="linux" ;;
    *)      echo "TaxaTag supports macOS and Linux from this script; use the .bat file on Windows." >&2
            exit 1 ;;
esac

VENV="$HERE/bin/$PLATFORM_DIR/python_env"

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

if [ ! -x "$VENV/bin/python" ]; then
    PYTHON="$(find_python)" || {
        echo "TaxaTag needs Python 3.11 or newer." >&2
        echo "Install it, then run this script again." >&2
        exit 1
    }
    echo "Setting up TaxaTag for the first time. This takes a minute or two..."
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip --quiet
    "$VENV/bin/python" -m pip install -r "$HERE/requirements.txt" --quiet
    echo "Setup finished."
fi

# Bundled tools lose their executable bit when a release is unzipped from a
# Windows-formatted archive, which shows up much later as "permission denied".
for tool in "$HERE/bin/$PLATFORM_DIR"/*/bin/* "$HERE/bin/$PLATFORM_DIR"/*/*; do
    [ -f "$tool" ] && [ ! -x "$tool" ] && chmod +x "$tool" 2>/dev/null || true
done

exec "$VENV/bin/python" "$HERE/taxatag.py" "$@"
