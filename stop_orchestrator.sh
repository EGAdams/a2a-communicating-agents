#!/bin/bash
# Stop the Orchestrator Agent

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

# Check for PID file in logs directory
if [ -f "$LOG_DIR/orchestrator.pid" ]; then
    PID=$(cat "$LOG_DIR/orchestrator.pid")

    if ps -p $PID > /dev/null 2>&1; then
        echo "Stopping Orchestrator Agent (PID: $PID)..."
        kill $PID
        rm "$LOG_DIR/orchestrator.pid"
        echo "Orchestrator Agent stopped"
    else
        echo "Orchestrator Agent is not running (stale PID: $PID)"
        rm "$LOG_DIR/orchestrator.pid"
    fi
else
    # Fallback: try to kill by process name
    echo "No PID file found, attempting to stop by process name..."
    pkill -f "orchestrator_agent/main.py"
    if [ $? -eq 0 ]; then
        echo "Orchestrator Agent stopped (by process name)"
    else
        echo "No orchestrator agent process found"
    fi
fi
