#!/bin/bash
#
# Start the Letta Bridge — relays WebSocket messages to Scissari via Letta API
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
PID_FILE="$LOG_DIR/letta_bridge.pid"
LOG_FILE="$LOG_DIR/letta_bridge.log"

mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️  Letta bridge already running (PID: $OLD_PID)"
        exit 0
    else
        echo "  Removing stale PID file"
        rm "$PID_FILE"
    fi
fi

echo "🚀 Starting Letta Bridge..."

if [ -z "${VIRTUAL_ENV:-}" ] || [ ! -x "$VIRTUAL_ENV/bin/python" ]; then
    echo "Activate the desired environment with 'so' before starting the Letta bridge." >&2
    exit 1
fi

PYTHON_BIN="$VIRTUAL_ENV/bin/python"
export PYTHONPATH="$SCRIPT_DIR/..:${PYTHONPATH:-}"

cd "$SCRIPT_DIR"
setsid nohup "$PYTHON_BIN" -u letta_bridge.py > "$LOG_FILE" 2>&1 </dev/null &
BRIDGE_PID=$!

echo "$BRIDGE_PID" > "$PID_FILE"
sleep 2

if ps -p "$BRIDGE_PID" > /dev/null 2>&1; then
    echo "✅ Letta bridge started (PID: $BRIDGE_PID)"
    echo "   Log: $LOG_FILE"
else
    echo "❌ Letta bridge failed to start"
    echo "   Check log: $LOG_FILE"
    rm "$PID_FILE"
    exit 1
fi
