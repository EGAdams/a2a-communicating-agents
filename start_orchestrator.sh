#!/bin/bash
# Start the Orchestrator Agent

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLANNER_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
A2A_ROOT="$PLANNER_ROOT/a2a_communicating_agents"

cd "$A2A_ROOT"

echo "Starting Orchestrator Agent..."

# Check if already running
if [ -f "$PLANNER_ROOT/logs/orchestrator.pid" ]; then
    PID=$(cat "$PLANNER_ROOT/logs/orchestrator.pid")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Orchestrator Agent is already running (PID: $PID)"
        exit 1
    fi
fi

# Create logs directory if it doesn't exist
mkdir -p "$PLANNER_ROOT/logs"

# Start the agent in the selected venv environment (same one used by `so`)
if [ -z "${VIRTUAL_ENV:-}" ] || [ ! -x "$VIRTUAL_ENV/bin/python" ]; then
    echo "No active virtualenv Python found. Activate the desired env with 'so' before starting the orchestrator." >&2
    exit 1
fi

PYTHON_BIN="$VIRTUAL_ENV/bin/python"
export PYTHONPATH="$PLANNER_ROOT:${PYTHONPATH:-}"
setsid nohup "$PYTHON_BIN" -u "$A2A_ROOT/orchestrator_agent/main.py" >> "$PLANNER_ROOT/logs/orchestrator.log" 2>&1 </dev/null &
PID=$!

# Save PID
echo "$PID" > "$PLANNER_ROOT/logs/orchestrator.pid"

echo "Orchestrator Agent started (PID: $PID)"
echo "Logs: $PLANNER_ROOT/logs/orchestrator.log"
