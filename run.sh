#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# D&D Character Manager — Portable Bootstrap
# Plug in the thumb drive, run this script. It handles venv + deps + startup.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

SCRIPT_DIR="$(pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PORT="${PORT:-8300}"

# ── 1. Find a working Python 3 ─────────────────────────────────────────────
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        v=$("$candidate" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo "")
        if [[ "$v" == "(3,"* ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "ERROR: No Python 3 found. Install python3 (3.10+) and try again."
    exit 1
fi
echo "→ Using: $($PYTHON --version)"

# ── 2. Create venv (if missing) ────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    echo "→ Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
    echo "  (packages will install on next step)"
else
    echo "→ venv found, skipping creation"
fi

# ── 3. Install/upgrade deps ────────────────────────────────────────────────
echo "→ Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "  Done."

# ── 4. Verify data directory ───────────────────────────────────────────────
if [[ ! -f "$SCRIPT_DIR/data/characters.db" ]]; then
    echo "ERROR: data/characters.db not found. Backup may be incomplete."
    exit 1
fi
echo "→ Database: $(du -h "$SCRIPT_DIR/data/characters.db" | cut -f1)"

# ── 5. Start server ────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  D&D Character Manager starting on http://0.0.0.0:$PORT"
echo "  Open http://localhost:$PORT in your browser"
echo "════════════════════════════════════════════════════════════"
echo ""
"$VENV_DIR/bin/python" -c "
import uvicorn
uvicorn.run('main:app', host='0.0.0.0', port=$PORT)
"
