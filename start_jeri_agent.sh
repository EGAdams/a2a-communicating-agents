#!/bin/bash
# Start the Jeri Agent (TypeScript/Node)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Jeri Agent..."

mkdir -p logs

if [ -f logs/jeri_agent.pid ]; then
    PID=$(cat logs/jeri_agent.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Jeri Agent is already running (PID: $PID)"
        exit 1
    else
        rm -f logs/jeri_agent.pid
    fi
fi

cd jeri_agent

if [ ! -d node_modules ]; then
    echo "Installing Jeri dependencies..."
    npm install
fi

echo "Building Jeri..."
npm run build

echo "Launching Jeri..."
cd ..
setsid nohup node --enable-source-maps jeri_agent/dist/index.js >> logs/jeri_agent.log 2>&1 </dev/null &
PID=$!
echo "$PID" > logs/jeri_agent.pid

echo "Jeri Agent started (PID: $PID)"
echo "Logs: logs/jeri_agent.log"
