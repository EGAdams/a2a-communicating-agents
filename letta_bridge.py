#!/usr/bin/env python3
"""
Letta Bridge — WebSocket ↔ Letta API adapter.

Subscribes to the WebSocket topic(s) declared in the letta agent card
(agent_messaging/agents/letta/agent.json). When the orchestrator sends a
JSON-RPC agent.execute_task payload there, this bridge:

  1. Extracts params.description (the natural-language task)
  2. Creates a Letta conversation with Scissari
  3. Sends the description as a user message
  4. Polls until the run completes (max LETTA_TIMEOUT_SEC)
  5. Fetches Scissari's reply from the conversation messages
  6. Posts the reply back to the 'orchestrator' WebSocket topic

Run this as a background daemon alongside the WebSocket server and orchestrator:
  source .venv/bin/activate
  python letta_bridge.py &
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup — works when run from any directory
# ---------------------------------------------------------------------------
BRIDGE_DIR = Path(__file__).resolve().parent          # a2a_communicating_agents/ (project root)
PLANNER_ROOT = BRIDGE_DIR                              # project root is the equivalent

sys.path.insert(0, str(BRIDGE_DIR.parent))  # for: from a2a_communicating_agents.X import Y
sys.path.insert(0, str(BRIDGE_DIR))         # for: from rag_system.X import Y

from dotenv import load_dotenv
load_dotenv(dotenv_path=BRIDGE_DIR / ".env", override=True)

# ---------------------------------------------------------------------------
# Config (all overridable via environment variables)
# ---------------------------------------------------------------------------
WEBSOCKET_URL = os.getenv("A2A_WEBSOCKET_URL", "ws://localhost:3030")
LETTA_BASE_URL = os.getenv("LETTA_BASE_URL", "http://100.80.49.10:8283")
SCISSARI_AGENT_ID = os.getenv(
    "SCISSARI_AGENT_ID", "agent-5955b0c2-7922-4ffe-9e43-b116053b80fa"
)
LETTA_TIMEOUT_SEC = int(os.getenv("LETTA_TIMEOUT_SEC", "120"))
RECONNECT_DELAY_SEC = int(os.getenv("BRIDGE_RECONNECT_DELAY", "5"))
AGENT_ID = "letta-bridge"

# Topics from the letta agent card (subscribe to all of them)
LETTA_AGENT_CARD = BRIDGE_DIR / "agent_messaging" / "agents" / "letta" / "agent.json"
try:
    _card = json.loads(LETTA_AGENT_CARD.read_text())
    LISTEN_TOPICS: list[str] = _card.get("topics") or ["memory", "general"]
except Exception:
    LISTEN_TOPICS = ["memory", "general"]

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [letta-bridge] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("letta-bridge")


# ---------------------------------------------------------------------------
# Letta helpers
# ---------------------------------------------------------------------------

def _letta_client():
    """Return a connected letta_client.Letta instance."""
    from letta_client import Letta  # type: ignore
    return Letta(base_url=LETTA_BASE_URL)


def _send_to_scissari_and_wait(description: str) -> str:
    """
    Synchronous: send description to Scissari, block until run completes,
    return her response text.

    Raises RuntimeError if the run fails or times out.
    """
    client = _letta_client()

    # Letta 0.16.3 quirk: must create a real conversation (not 'default')
    conv = client.conversations.create(agent_id=SCISSARI_AGENT_ID)
    log.info("Created conversation %s for Scissari", conv.id)

    result = client.conversations.messages.create(
        conv.id,
        messages=[{"role": "user", "content": description}],
    )

    # Extract run_id
    run_id: Optional[str] = (
        getattr(result, "id", None)
        or getattr(result, "run_id", None)
    )
    if run_id is None and hasattr(result, "run"):
        run_id = getattr(getattr(result, "run", None), "id", None)

    if not run_id:
        log.warning("Could not extract run_id from response; returning raw result")
        return str(result)

    log.info("Run ID: %s — polling for completion (max %ss)", run_id, LETTA_TIMEOUT_SEC)

    # Poll until completed / failed
    deadline = time.time() + LETTA_TIMEOUT_SEC
    run = None
    while time.time() < deadline:
        run = client.runs.retrieve(run_id)
        status = getattr(run, "status", None)
        if status in ("completed", "failed", "error"):
            break
        time.sleep(3)

    if run is None:
        raise RuntimeError("runs.retrieve returned None")

    status = getattr(run, "status", "unknown")
    if status != "completed":
        raise RuntimeError(f"Run ended with status '{status}' (expected 'completed')")

    log.info("Run completed. Fetching conversation messages...")

    # Letta 0.16.3: runs.messages only has user_message; use conversations.messages
    messages_page = client.conversations.messages.list(conv.id, limit=20)
    if hasattr(messages_page, "getPaginatedItems"):
        messages = messages_page.getPaginatedItems()
    elif hasattr(messages_page, "items"):
        messages = messages_page.items
    else:
        messages = list(messages_page) if messages_page else []

    # Collect assistant message text (skip user messages and tool calls)
    reply_parts: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", None) or (
            getattr(msg, "message_type", "")
        )
        if role in ("assistant", "assistant_message"):
            content = getattr(msg, "content", None) or getattr(msg, "text", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        reply_parts.append(part["text"])
                    elif hasattr(part, "text"):
                        reply_parts.append(part.text)
            elif content:
                reply_parts.append(str(content))

    if reply_parts:
        return "\n".join(reply_parts)

    # Fallback: check runs.steps for any output text
    try:
        steps_page = client.runs.steps.list(run_id, limit=10)
        steps = (
            steps_page.getPaginatedItems()
            if hasattr(steps_page, "getPaginatedItems")
            else list(steps_page)
        )
        for step in steps:
            output = getattr(step, "output", None) or getattr(step, "result", None)
            if output and isinstance(output, str):
                return output
    except Exception as exc:
        log.warning("Could not read run steps: %s", exc)

    return "(Scissari responded but no extractable text was found in the conversation)"


# ---------------------------------------------------------------------------
# WebSocket message handling
# ---------------------------------------------------------------------------

def _extract_description(content: str) -> Optional[str]:
    """
    Parse a JSON-RPC agent.execute_task payload and return params.description.
    Falls back to the raw content if it's not JSON-RPC.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content  # treat raw text as description

    method = data.get("method", "")
    if "execute_task" in method:
        params = data.get("params") or {}
        return params.get("description") or params.get("task") or str(params)

    # Not a JSON-RPC delegation — treat the whole content as text
    return content


async def _handle_letta_message(ws, raw_envelope: dict) -> None:
    """Process one incoming message envelope from the letta/memory topic."""
    content = raw_envelope.get("content", "")
    from_agent = raw_envelope.get("from_agent", "unknown")
    topic = raw_envelope.get("topic", "?")

    log.info(
        "Received from '%s' on topic '%s': %.200s",
        from_agent, topic, content,
    )

    description = _extract_description(content)
    if not description:
        log.warning("Empty description — skipping")
        return

    log.info("Forwarding to Scissari: %.200s", description)

    try:
        reply = await asyncio.get_event_loop().run_in_executor(
            None, _send_to_scissari_and_wait, description
        )
        log.info("Scissari replied: %.300s", reply)
    except Exception as exc:
        reply = f"[Letta bridge error] Could not reach Scissari: {exc}"
        log.error("Error contacting Scissari: %s", exc)

    # Post Scissari's reply back to the orchestrator topic
    envelope = json.dumps({
        "type": "send",
        "topic": "orchestrator",
        "content": f"**Scissari (via Letta bridge):** {reply}",
        "to_agent": "board",
        "from_agent": AGENT_ID,
        "priority": "normal",
        "metadata": {"relayed_from": SCISSARI_AGENT_ID},
    })

    try:
        await ws.send(envelope)
        ack = await asyncio.wait_for(ws.recv(), timeout=5)
        log.info("Relay ACK: %s", ack)
    except Exception as exc:
        log.error("Failed to relay Scissari's reply: %s", exc)


# ---------------------------------------------------------------------------
# WebSocket connection loop
# ---------------------------------------------------------------------------

async def _run_bridge() -> None:
    """Main bridge loop — connects, subscribes, processes messages, reconnects."""
    try:
        import websockets  # type: ignore
    except ImportError:
        log.error("websockets package not installed. Run: pip install websockets")
        sys.exit(1)

    while True:
        log.info("Connecting to %s ...", WEBSOCKET_URL)
        try:
            async with websockets.connect(
                WEBSOCKET_URL,
                open_timeout=10,
                ping_interval=25,
                ping_timeout=60,
            ) as ws:
                # Register
                await ws.send(json.dumps({"type": "register", "agent_id": AGENT_ID}))
                reg_ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                log.info("Registered: %s", reg_ack)

                # Subscribe to all letta topics
                for topic in LISTEN_TOPICS:
                    await ws.send(json.dumps({"type": "subscribe", "topic": topic}))
                    sub_ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    log.info("Subscribed to '%s': %s", topic, sub_ack)

                log.info(
                    "Bridge ready. Listening on topics: %s → forwarding to Scissari (%s)",
                    LISTEN_TOPICS, SCISSARI_AGENT_ID,
                )

                async for raw in ws:
                    try:
                        envelope = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Skip ACKs and non-message envelopes
                    if envelope.get("type") != "message":
                        continue

                    # Skip echoes from ourselves
                    if envelope.get("from_agent") == AGENT_ID:
                        continue

                    # Handle in a task so keepalives aren't blocked by Letta I/O
                    asyncio.create_task(_handle_letta_message(ws, envelope))

        except Exception as exc:
            log.warning("Bridge disconnected: %s — reconnecting in %ss", exc, RECONNECT_DELAY_SEC)
            await asyncio.sleep(RECONNECT_DELAY_SEC)


def main() -> None:
    log.info("Letta Bridge starting")
    log.info("  WebSocket: %s", WEBSOCKET_URL)
    log.info("  Letta server: %s", LETTA_BASE_URL)
    log.info("  Scissari agent: %s", SCISSARI_AGENT_ID)
    log.info("  Listening topics: %s", LISTEN_TOPICS)
    try:
        asyncio.run(_run_bridge())
    except KeyboardInterrupt:
        log.info("Bridge stopped.")


if __name__ == "__main__":
    main()
