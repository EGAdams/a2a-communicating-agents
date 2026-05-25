#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/../logs/letta_bridge.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID"
        echo "✅ Letta bridge stopped (PID: $PID)"
    else
        echo "ℹ️  Letta bridge was not running"
    fi
    rm "$PID_FILE"
else
    echo "ℹ️  No PID file found"
fi
