#!/bin/bash
#
# Start WebSocket Message Board Server
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
PID_FILE="$LOG_DIR/websocket_server.pid"
LOG_FILE="$LOG_DIR/websocket.log"

# Create logs directory
mkdir -p "$LOG_DIR"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️  WebSocket server already running (PID: $OLD_PID)"
        exit 0
    else
        echo "  Removing stale PID file"
        rm "$PID_FILE"
    fi
fi

echo "🚀 Starting WebSocket Message Board Server..."

# Activate the correct venv
source "$SCRIPT_DIR/../nonprofit_finance_db/receipt_scanning_tools/venv/bin/activate"

# Start server in background
cd "$SCRIPT_DIR"
setsid nohup python -u agent_messaging/websocket_server.py > "$LOG_FILE" 2>&1 </dev/null &
SERVER_PID=$!

# Save PID
echo "$SERVER_PID" > "$PID_FILE"

# Wait a moment and check if it's running
sleep 2

if ps -p "$SERVER_PID" > /dev/null 2>&1; then
    echo "✅ WebSocket server started (PID: $SERVER_PID)"
    echo "   Log: $LOG_FILE"
    echo "   URL: ws://localhost:3030"
    exit 0
else
    echo "❌ WebSocket server failed to start"
    echo "   Check log: $LOG_FILE"
    rm "$PID_FILE"
    exit 1
fi
