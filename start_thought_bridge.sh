#!/usr/bin/env bash
# Start the Thought Bridge WebSocket server.
# Listens on 0.0.0.0:8765 — producer (lettabot) on localhost, browsers via Tailscale.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="/home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/venv/bin/python3"
LOG_FILE="$SCRIPT_DIR/logs/thought_bridge.log"

mkdir -p "$SCRIPT_DIR/logs"

echo "Starting Thought Bridge..."
echo "  Producer URL : ws://localhost:8765"
echo "  Browser URL  : ws://100.72.158.63:8765"
echo "  Log          : $LOG_FILE"
echo ""

exec "$VENV_PYTHON" "$SCRIPT_DIR/thought_bridge.py" 2>&1 | tee "$LOG_FILE"
