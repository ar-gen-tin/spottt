#!/bin/bash
# Spottt setup — creates a Python venv and installs all dependencies.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=== Spottt Setup ==="

# Create venv if needed
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

# Also ensure ascii-art deps are available
if [ -f "$SCRIPT_DIR/ascii-art/scripts/requirements.txt" ]; then
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/ascii-art/scripts/requirements.txt" 2>/dev/null || true
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Usage:"
echo "  1. Set your Spotify Client ID:"
echo '     export SPOTIFY_CLIENT_ID="your_client_id_here"'
echo ""
echo "  2. Run Spottt:"
echo "     $VENV_DIR/bin/python $SCRIPT_DIR/run.py"
echo ""
echo "  Or create an alias:"
echo "     alias spottt='$VENV_DIR/bin/python $SCRIPT_DIR/run.py'"
echo ""
echo "Get a Client ID at: https://developer.spotify.com/dashboard"
echo "Set redirect URI to: http://127.0.0.1:8888/callback"
