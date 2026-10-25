#!/usr/bin/env python3
"""
Helper script to send messages to agents via the WebSocket message board.

Uses a single async WebSocket session: subscribes to the response topic first
(receiving any existing history), sends the task, then waits for new replies.
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional, Tuple

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets", file=sys.stderr)
    sys.exit(1)

CURRENT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = CURRENT_DIR.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.append(str(WORKSPACE_ROOT))

from a2a_communicating_agents.agent_messaging import create_jsonrpc_request

WS_URL = os.getenv("A2A_WS_URL", os.getenv("WEBSOCKET_URL", "ws://127.0.0.1:3030"))

TOPIC_MAPPING = {
    "dashboard-agent": "ops",
    "orchestrator-agent": "orchestrator",
    "jeri-agent": "january-statements",
}

# Jeri replies on orchestrator topic, not back on its own topic.
RESPONSE_TOPIC_MAPPING = {
    "jeri-agent": "orchestrator",
}

RESPONSE_HINTS = {
    "dashboard-agent": "dashboard",
    "orchestrator-agent": "orchestrator",
    "jeri-agent": "jeri-agent",
}

DEFAULT_WAIT_SECONDS = float(os.getenv("AGENT_MESSAGE_WAIT_SECONDS", "8"))


async def _send_and_receive(
    agent_id: str,
    message: str,
    *,
    timeout: float = DEFAULT_WAIT_SECONDS,
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Open one WebSocket session, subscribe to the response topic, send the
    task, then wait up to `timeout` seconds for a reply.

    Returns: (status_line, sender, response_content)
    """
    outbound_topic = TOPIC_MAPPING.get(agent_id, "general")
    response_topic = RESPONSE_TOPIC_MAPPING.get(agent_id, outbound_topic)
    expected_sender = RESPONSE_HINTS.get(agent_id, "")
    watcher_id = f"send-watcher-{uuid.uuid4().hex[:6]}"

    async with websockets.connect(
        WS_URL,
        max_size=10 * 1024 * 1024,
        ping_interval=30,
        ping_timeout=60,
    ) as ws:
        # Register
        await ws.send(json.dumps({"type": "register", "agent_id": watcher_id}))
        reg = json.loads(await ws.recv())
        if reg.get("type") != "registered":
            raise ConnectionError(f"Registration failed: {reg}")

        # Subscribe to response topic — server sends up to 10 history messages immediately.
        # Drain all of those so we only react to messages arriving after our send.
        await ws.send(json.dumps({"type": "subscribe", "topic": response_topic}))
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
        request = create_jsonrpc_request(
            method="agent.execute_task",
            params={"description": message, "from": "dashboard-ui"},
        )
        await ws.send(json.dumps({
            "type": "send",
            "topic": outbound_topic,
            "to_agent": "board",
            "from_agent": "dashboard-ui",
            "content": request,
            "priority": "normal",
            "metadata": {},
        }))

        status = f"Message sent to {agent_id} on topic '{outbound_topic}'"

        # Drain the "sent" ACK (and any other non-message frames) so the reply
        # loop only sees genuine "message" frames from the response topic.
        try:
            ack_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            ack = json.loads(ack_raw)
            if ack.get("type") not in ("sent", "subscribed"):
                # Unexpected frame — put it back by handling it inline
                pass
        except asyncio.TimeoutError:
            pass

        # Everything arriving from here on is genuinely new — no content filter needed.
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
            if msg.get("topic") != response_topic:
                continue

            sender = msg.get("from_agent", "unknown")
            if expected_sender and expected_sender.lower() not in sender.lower():
                continue

            return status, sender, msg.get("content", "")

    return status, None, None


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python send_agent_message.py <agent_id> <message>", file=sys.stderr)
        sys.exit(1)

    agent_id = sys.argv[1]
    message = sys.argv[2]

    response_topic = RESPONSE_TOPIC_MAPPING.get(
        agent_id, TOPIC_MAPPING.get(agent_id, "general")
    )

    try:
        status, sender, response = asyncio.run(
            _send_and_receive(agent_id, message)
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(status)
    if response:
        print(f"\nLatest response from {sender} on topic '{response_topic}':\n{response}")
    else:
        print(
            f"\nNo response detected on topic '{response_topic}' within "
            f"{DEFAULT_WAIT_SECONDS:.0f}s. Check orchestrator logs if this persists."
        )


if __name__ == "__main__":
    main()
