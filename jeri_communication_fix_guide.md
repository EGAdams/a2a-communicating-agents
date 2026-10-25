# Jeri Communication Fix Guide

**Status: RESOLVED 2026-05-26** — all steps below reflect the applied fix.

---

## What was broken

1. **`.env` had wrong WebSocket URL** — `A2A_WS_URL=ws://100.72.158.63:3030` (Scissari Tailscale IP).
   Jeri connected once (loopback default), subscribed, then retried with the env var URL and
   entered a permanent `ECONNREFUSED` loop.

2. **`send_agent_message.py` used polling** — `read_messages()` calls `poll_messages()` which
   always returns `[]` on WebSocket transport. Responses were never detected.

3. **Wrong response topic** — Jeri replies on `orchestrator` topic, not `january-statements`.
   The watcher was reading the wrong topic.

---

## Fix applied

### `.env`
```
# Before (broken — points at Scissari):
A2A_WS_URL=ws://100.72.158.63:3030
WEBSOCKET_URL=ws://100.72.158.63:3030

# After (fixed — localhost):
A2A_WS_URL=ws://127.0.0.1:3030
WEBSOCKET_URL=ws://127.0.0.1:3030
```

### `agent_messaging/send_agent_message.py`
Rewritten as a single `asyncio.run()` session using direct `websockets.connect()`:
- Subscribes to the response topic first (drains history)
- Sends the task
- Waits for live messages from `jeri-agent` on the `orchestrator` topic

---

## Quick recovery sequence

```bash
cd /home/adamsl/a2a-communicating-agents
bash stop_all.sh || true

# Check .env WebSocket URL — MUST be 127.0.0.1 for local operation
grep A2A_WS_URL .env

so    # activate venv (shell alias)
bash start_websocket_server.sh
ss -ltnp | grep 3030              # confirm port open

bash start_orchestrator.sh
bash start_jeri_agent.sh

# Wait 2s then test
sleep 2
cd /home/adamsl
python3 a2a_communicating_agents/agent_messaging/send_agent_message.py jeri-agent "health check"
```

Expected:
```
Message sent to jeri-agent on topic 'january-statements'

Latest response from jeri-agent on topic 'orchestrator':
{"jsonrpc":"2.0","result":{"status":"success","message":"Statement readiness analysis complete.",...}}
```

---

## Diagnostic steps

### 1) Verify processes
```bash
ps -ef | grep -E "websocket_server.py|orchestrator_agent/main.py|jeri_agent/dist/index.js" | grep -v grep
```

### 2) Confirm port 3030
```bash
ss -ltnp | grep 3030
```

### 3) Check Jeri is subscribed
```bash
tail -n 20 /home/adamsl/a2a-communicating-agents/logs/jeri_agent.log
# Look for: "Subscribed to topic 'january-statements'"
# Bad sign: repeated "ECONNREFUSED 100.72.158.63" → .env URL is wrong
```

### 4) Check WebSocket server delivered the message
```bash
tail -n 30 /home/adamsl/a2a-communicating-agents/logs/websocket.log
# Look for: "Sent message on topic 'january-statements' to 1/1 subscribers"
# Then:     "Agent 'jeri-agent' sending to topic 'orchestrator'"
```

### 5) Install missing Python deps (if needed)
```bash
so
pip install websockets chromadb letta sentence-transformers
```
Note: `chromadb` and `sentence-transformers` are optional — only needed for the RAG
fallback transport. WebSocket communication works without them.

---

## Cross-machine note (Scissari ↔ this machine)

| Scenario | `A2A_WS_URL` to set |
|----------|---------------------|
| Local (all on this machine) | `ws://127.0.0.1:3030` |
| WS server here, Scissari agents connect | Set `ws://100.72.34.38:3030` on Scissari |
| WS server on Scissari, local agents connect | `ws://100.72.158.63:3030` |

This machine's Tailscale IP: `100.72.34.38`
Scissari's Tailscale IP: `100.72.158.63`
