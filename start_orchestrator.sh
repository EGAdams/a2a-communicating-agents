#!/bin/bash
# Start the Orchestrator Agent

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A2A_ROOT="$SCRIPT_DIR"
LOG_DIR="$SCRIPT_DIR/logs"

cd "$A2A_ROOT"

echo "Starting Orchestrator Agent..."

# Check if already running
if [ -f "$LOG_DIR/orchestrator.pid" ]; then
    PID=$(cat "$LOG_DIR/orchestrator.pid")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Orchestrator Agent is already running (PID: $PID)"
        exit 1
    fi
fi

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Activate project venv if not already in a venv
if [ -z "${VIRTUAL_ENV:-}" ] || [ ! -x "$VIRTUAL_ENV/bin/python" ]; then
    if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
        source "$SCRIPT_DIR/venv/bin/activate"
    else
        echo "No active virtualenv Python found and no project venv at $SCRIPT_DIR/venv." >&2
        exit 1
    fi
fi

PYTHON_BIN="$VIRTUAL_ENV/bin/python"
export PYTHONPATH="$A2A_ROOT:${PYTHONPATH:-}"
setsid nohup "$PYTHON_BIN" -u "$A2A_ROOT/orchestrator_agent/main.py" >> "$LOG_DIR/orchestrator.log" 2>&1 </dev/null &
PID=$!

# Save PID
echo "$PID" > "$LOG_DIR/orchestrator.pid"

echo "Orchestrator Agent started (PID: $PID)"
echo "Logs: $LOG_DIR/orchestrator.log"
