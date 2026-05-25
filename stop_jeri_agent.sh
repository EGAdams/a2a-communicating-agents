#!/bin/bash
# Stop the Jeri Agent

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping Jeri Agent..."

if [ ! -f logs/jeri_agent.pid ]; then
    echo "Jeri Agent PID file not found"
    exit 0
fi

PID=$(cat logs/jeri_agent.pid)
if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Jeri Agent not running"
    rm -f logs/jeri_agent.pid
    exit 0
fi

kill "$PID"
for i in {1..5}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "Jeri Agent stopped"
        rm -f logs/jeri_agent.pid
        exit 0
    fi
    sleep 1
done

if ps -p "$PID" > /dev/null 2>&1; then
    kill -9 "$PID"
fi

rm -f logs/jeri_agent.pid
echo "Jeri Agent stopped"
