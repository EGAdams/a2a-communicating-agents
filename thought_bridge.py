#!/usr/bin/env python3
"""
Thought Bridge — WebSocket fanout server.

Architecture (GoF Observer / Mediator):
  Producer (lettabot) → [ThoughtBridge] → Consumer (browser)

Connection protocol
  - Producer: connect → send {"role":"producer"} → send ThoughtEvent JSON messages
  - Consumer: connect → receive ThoughtEvent JSON messages (no initial send required)

Endpoints (both on same port, role-negotiated):
  ws://localhost:8765          — producer connects here (same host as lettabot)
  ws://100.72.158.63:8765      — browser clients connect via Tailscale
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

try:
    import websockets
    from websockets.asyncio.server import serve, ServerConnection
except ImportError:
    print("websockets package required: pip install websockets", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("thought-bridge")

HOST = "0.0.0.0"
PORT = 8765
TAILSCALE_IP = "100.72.158.63"

# Live connection sets — modified only inside the event loop
_consumers: set[ServerConnection] = set()
_producers: set[ServerConnection] = set()


async def _fanout(message: str) -> None:
    """Broadcast a raw JSON string to every consumer."""
    if not _consumers:
        return
    results = await asyncio.gather(
        *[c.send(message) for c in list(_consumers)],
        return_exceptions=True,
    )
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            # Consumer disconnected mid-send; cleanup happens in its own handler
            logger.debug(f"Fanout error (consumer likely closed): {r}")


def _system_event(text: str, kind: str = "system") -> str:
    return json.dumps({
        "type": "thought",
        "kind": kind,
        "text": text,
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "agentId": "bridge",
    })


async def _handle_producer(ws: ServerConnection) -> None:
    """Handle a producer connection: fan every message out to consumers."""
    _producers.add(ws)
    logger.info(
        f"Producer connected  {ws.remote_address}  "
        f"(producers={len(_producers)}, consumers={len(_consumers)})"
    )
    # Notify consumers that a producer came online
    await _fanout(_system_event("Producer connected — thought stream live", "connected"))

    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode()
            logger.debug(f"Fanout: {raw[:120]}")
            await _fanout(raw)
    except Exception as e:
        logger.debug(f"Producer receive error: {e}")
    finally:
        _producers.discard(ws)
        logger.info(f"Producer disconnected  {ws.remote_address}")
        await _fanout(_system_event("Producer disconnected — stream paused", "disconnected"))


async def _handle_consumer(ws: ServerConnection) -> None:
    """Handle a browser/consumer connection: keep alive and receive nothing useful."""
    _consumers.add(ws)
    logger.info(
        f"Consumer connected  {ws.remote_address}  "
        f"(producers={len(_producers)}, consumers={len(_consumers)})"
    )
    # Greet with current state
    state_msg = "connected" if _producers else "disconnected"
    await ws.send(_system_event(
        "Bridge connected" if _producers else "Bridge connected — waiting for producer",
        state_msg,
    ))

    try:
        # Consumers don't send anything; just keep the connection alive
        async for _ in ws:
            pass
    except Exception:
        pass
    finally:
        _consumers.discard(ws)
        logger.info(f"Consumer disconnected  {ws.remote_address}")


async def _handle_connection(ws: ServerConnection) -> None:
    """
    Role-negotiation handler.

    Default role is consumer.  If the first message is {"role":"producer"},
    the connection is promoted to producer mode.
    """
    # Register as consumer immediately so browsers get events without delay
    _consumers.add(ws)

    try:
        first_raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
        if isinstance(first_raw, bytes):
            first_raw = first_raw.decode()
        data = json.loads(first_raw)

        if data.get("role") == "producer":
            # Upgrade to producer
            _consumers.discard(ws)
            await _handle_producer(ws)
            return
        # else: treat first_raw as a consumer message (ignored)
    except asyncio.TimeoutError:
        # No message within 1.5s → confirmed consumer
        pass
    except (json.JSONDecodeError, Exception):
        pass

    # Remain as consumer — remove from set, let _handle_consumer re-add cleanly
    _consumers.discard(ws)
    await _handle_consumer(ws)


async def main() -> None:
    logger.info(f"Starting Thought Bridge on {HOST}:{PORT}")
    logger.info(f"  Producer URL  : ws://localhost:{PORT}")
    logger.info(f"  Consumer URL  : ws://{TAILSCALE_IP}:{PORT}")

    stop = asyncio.Future()

    def _shutdown(*_):
        logger.info("Shutdown signal received")
        stop.set_result(None)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    async with serve(_handle_connection, HOST, PORT):
        logger.info("Bridge ready.")
        await stop

    logger.info("Bridge stopped.")


if __name__ == "__main__":
    asyncio.run(main())
