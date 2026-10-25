"""
Letta tool: send a task to the Jeri financial-document agent and return her response.

Drop this file on the Windows machine and register `talk_to_jeri` with the Letta agent.
Requires: pip install websockets

Environment variables (optional):
  JERI_WS_URL   — WebSocket board URL (default: ws://100.72.34.38:3030, Tailscale IP)
  JERI_TIMEOUT  — seconds to wait for a reply (default: 15)
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_WS_URL = os.getenv("JERI_WS_URL", "ws://100.72.34.38:3030")
_TIMEOUT = float(os.getenv("JERI_TIMEOUT", "15"))

_OUTBOUND_TOPIC = "january-statements"
_RESPONSE_TOPIC = "orchestrator"
_EXPECTED_SENDER = "jeri-agent"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_jsonrpc(message: str) -> str:
    return json.dumps({
        "jsonrpc": "2.0",
        "method": "agent.execute_task",
        "params": {"description": message, "from": "letta-windows"},
        "id": 1,
    })


async def _send_and_receive(message: str, ws_url: str, timeout: float) -> str:
    try:
        import websockets  # type: ignore
    except ImportError:
        return "ERROR: websockets not installed on this machine. Run: pip install websockets"

    caller_id = f"letta-tool-{uuid.uuid4().hex[:6]}"

    async with websockets.connect(
        ws_url,
        max_size=10 * 1024 * 1024,
        ping_interval=30,
        ping_timeout=60,
    ) as ws:
        # Register
        await ws.send(json.dumps({"type": "register", "agent_id": caller_id}))
        reg = json.loads(await ws.recv())
        if reg.get("type") != "registered":
            return f"ERROR: WebSocket registration failed: {reg}"

        # Subscribe to orchestrator topic and drain history
        await ws.send(json.dumps({"type": "subscribe", "topic": _RESPONSE_TOPIC}))
        drain_deadline = asyncio.get_event_loop().time() + 1.5
        while True:
            remaining = drain_deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

        # Send the task
        await ws.send(json.dumps({
            "type": "send",
            "topic": _OUTBOUND_TOPIC,
            "to_agent": "board",
            "from_agent": caller_id,
            "content": _make_jsonrpc(message),
            "priority": "normal",
            "metadata": {},
        }))

        # Drain the sent ACK
        try:
            await asyncio.wait_for(ws.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        # Wait for Jeri's reply on the orchestrator topic
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
            except asyncio.TimeoutError:
                continue

            msg = json.loads(raw)
            if msg.get("type") != "message":
                continue
            if msg.get("topic") != _RESPONSE_TOPIC:
                continue
            sender = msg.get("from_agent", "")
            if _EXPECTED_SENDER.lower() not in sender.lower():
                continue

            return msg.get("content", "(empty response)")

    return f"No response from Jeri within {timeout:.0f}s. Check that the A2A system is running on the host machine."


# ---------------------------------------------------------------------------
# Letta tool entry point
# ---------------------------------------------------------------------------

def talk_to_jeri(message: str) -> str:
    """
    Send a natural-language task to the Jeri financial-document agent and return her response.

    Jeri can analyze January bank statements, extract transactions, find anomalies,
    summarize statements, or prepare report snippets. Examples:
      - "summarize the January FNBO statement"
      - "find any anomalies in the January transactions"
      - "extract all transactions over $500"
      - "health check"

    Args:
        message: The task or question to send to Jeri.

    Returns:
        Jeri's response as a string, or an error message if she is unreachable.
    """
    return asyncio.run(_send_and_receive(message, _WS_URL, _TIMEOUT))


# ---------------------------------------------------------------------------
# CLI usage: python talk_to_jeri.py "your message here"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python talk_to_jeri.py <message>")
        sys.exit(1)

    result = talk_to_jeri(" ".join(sys.argv[1:]))
    print(result)
