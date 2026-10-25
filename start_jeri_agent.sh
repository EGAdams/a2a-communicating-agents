#!/bin/bash
# Start the Jeri Agent (TypeScript/Node)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Jeri Agent..."

mkdir -p logs

# Load .env so A2A_WS_URL and other env vars reach the Node process
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [ -f logs/jeri_agent.pid ]; then
    PID=$(cat logs/jeri_agent.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Jeri Agent is already running (PID: $PID)"
        exit 1
    else
        rm -f logs/jeri_agent.pid
    fi
fi

# Load nvm if node not in PATH
if ! command -v node &>/dev/null; then
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
fi

if ! command -v node &>/dev/null; then
    echo "❌ Node.js not found. Install via: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash && nvm install --lts"
    exit 1
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
