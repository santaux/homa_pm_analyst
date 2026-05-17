#!/bin/bash
# Homa Energy PM Analytics — one-time setup (macOS / Linux)
set -e

cd "$(dirname "$0")"

# 1. Extract the analytics database if still zipped
if [[ ! -f homa_pm_events.sqlite3 ]]; then
    if [[ -f homa_pm_events.sqlite3.zip ]]; then
        echo "Extracting homa_pm_events.sqlite3..."
        unzip -q homa_pm_events.sqlite3.zip
    else
        echo "ERROR: neither homa_pm_events.sqlite3 nor homa_pm_events.sqlite3.zip found." >&2
        exit 1
    fi
fi

# 2. Create venv and install MCP server deps
if [[ ! -d .venv ]]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
echo "Installing MCP server dependencies..."
.venv/bin/pip install -r mcp/requirements.txt -q

# 3. Start Metabase
if command -v docker >/dev/null 2>&1; then
    echo "Starting Metabase..."
    (cd metabase && docker compose up -d)
    echo "Metabase boot can take 60-90 s the first time. Watch progress with:"
    echo "  docker compose logs -f metabase"
else
    echo ""
    echo "⚠  Docker not found. Install Docker Desktop to use dashboards."
    echo "   You can still use Claude Code + MCP for data analysis."
fi

echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  1. Wait for Metabase to be healthy (about a minute on first boot)"
echo "  2. Dashboards:  http://localhost:3000  →  admin@homa.local / HomaAdmin1!"
echo "  3. Claude Code: cd $(pwd) && claude"
