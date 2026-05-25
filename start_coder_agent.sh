#!/bin/bash
# Start the Coder Agent

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Coder Agent..."

# Check if already running
if [ -f logs/coder_agent.pid ]; then
    PID=$(cat logs/coder_agent.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "Coder Agent is already running (PID: $PID)"
        exit 1
    fi
fi

# Create logs directory if it doesn't exist
mkdir -p logs

# Start the agent in the background using virtual environment
source /home/adamsl/planner/nonprofit_finance_db/receipt_scanning_tools/venv/bin/activate
setsid nohup python -u coder_agent/main.py >> logs/coder_agent.log 2>&1 </dev/null &
PID=$!

# Save PID
echo $PID > logs/coder_agent.pid

echo "Coder Agent started (PID: $PID)"
echo "Logs: logs/coder_agent.log"
