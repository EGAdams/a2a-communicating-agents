# A2A System — Quick Reference

**Last Updated**: 2026-05-26

---

## Services & Ports

| Service | Port / URL | Log | PID file |
|---------|-----------|-----|----------|
| WebSocket message board | `ws://127.0.0.1:3030` | `logs/websocket.log` | `logs/websocket_server.pid` |
| Orchestrator agent | — (no HTTP) | `logs/orchestrator.log` | `logs/orchestrator.pid` |
| Jeri agent (Node/TS) | — (WS client only) | `logs/jeri_agent.log` | `logs/jeri_agent.pid` |
| Coder agent | — | `logs/coder_agent.log` | `logs/coder_agent.pid` |
| Tester agent | — | `logs/tester_agent.log` | `logs/tester_agent.pid` |
| Letta bridge | — (optional) | `logs/letta_bridge.log` | — |

---

## Start / Stop

```bash
so                   # activate venv (shell alias for this machine)
bash start_all.sh    # start websocket → letta bridge → orchestrator → coder → tester → jeri
bash stop_all.sh     # stop all
```

Manual per-service (order matters — websocket must come first):
```bash
bash start_websocket_server.sh
bash start_orchestrator.sh
bash start_jeri_agent.sh
bash start_coder_agent.sh
bash start_tester_agent.sh
```

---

## Test Jeri Communication

```bash
so
cd /home/adamsl
python3 a2a_communicating_agents/agent_messaging/send_agent_message.py jeri-agent "health check"
```

Expected output:
```
Message sent to jeri-agent on topic 'january-statements'

Latest response from jeri-agent on topic 'orchestrator':
{"jsonrpc":"2.0","result":{"status":"success", ...}}
```

Jeri's capabilities (inferred from message text):
- `analyze_statement` — general readiness check (default)
- `summarize_statement` — list/count files
- `extract_transactions` — extraction preflight
- `find_anomalies` — flag unparseable files
- `prepare_report_snippet` — write artifact to `~/rol_finances/artifacts/jeri-agent/`

---

## Chat with Orchestrator

```bash
so
cd /home/adamsl/a2a-communicating-agents/agent_messaging
python3 orchestrator_chat.py
```

---

## Monitor Logs

```bash
tail -f logs/*.log              # all agents
tail -f logs/websocket.log      # message routing
tail -f logs/orchestrator.log   # routing decisions
tail -f logs/jeri_agent.log     # Jeri processing
```

---

## Verify Port / Processes

```bash
ss -ltnp | grep 3030            # WebSocket listening?
ps -ef | grep -E "websocket_server|orchestrator_agent|jeri_agent|coder_agent|tester_agent" | grep -v grep
```

---

## Troubleshooting

**Jeri connects then retries in a loop:**
Check `.env` — `A2A_WS_URL` must be `ws://127.0.0.1:3030` for local operation.
For remote Scissari cross-machine mode, change to `ws://100.72.158.63:3030`.

**Stale PID files after a crash:**
```bash
for f in logs/*.pid; do
    pid=$(cat "$f"); ps -p "$pid" > /dev/null 2>&1 || rm -f "$f"
done
```

**WebSocket on 3030 not listening:**
```bash
bash start_websocket_server.sh
```

**`send_agent_message.py` "Failed to initialize transport" errors:**
These are harmless noise from old asyncio-run() stale-loop detection. If the message
IS sent (line "Message sent to..."), communication succeeded. The script was rewritten
2026-05-26 to use a single async session; these errors should no longer appear.

**No response from Jeri:**
1. Check `logs/jeri_agent.log` — look for "Processed task" to confirm receipt.
2. Jeri replies on the `orchestrator` topic, not `january-statements`.
3. If no "Processed task": is Jeri subscribed? Look for "Subscribed to topic 'january-statements'".

---

## Environment

```
Project root:  /home/adamsl/a2a-communicating-agents/
Venv:          /home/adamsl/a2a-communicating-agents/venv/
Symlink alias: /home/adamsl/a2a_communicating_agents → above (for Python package imports)
.env:          /home/adamsl/a2a-communicating-agents/.env
Jeri data dir: /home/adamsl/rol_finances/readable_documents/bank_statements/january
Jeri artifacts: /home/adamsl/rol_finances/artifacts/jeri-agent/
```

---

## Cross-machine (Scissari / Tailscale)

This machine's Tailscale IP: `100.72.34.38`
Scissari (mom's machine) Tailscale IP: `100.72.158.63`

To run the WebSocket server on this machine and have Scissari agents connect:
- Set `A2A_WS_URL=ws://100.72.34.38:3030` on Scissari's `.env`
- Keep `ws://127.0.0.1:3030` here

To connect to a WebSocket server running on Scissari:
- Set `A2A_WS_URL=ws://100.72.158.63:3030` in this machine's `.env`
