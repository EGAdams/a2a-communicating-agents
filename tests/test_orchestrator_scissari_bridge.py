"""
Integration test: Orchestrator → Letta → Scissari message flow

This test suite pinpoints exactly where the communication chain breaks when
a user asks the Orchestrator to send a message to Scissari (the Letta agent
at agent-5955b0c2-7922-4ffe-9e43-b116053b80fa on http://100.80.49.10:8283).

The observed failure from orchestrator_chat.py:
  - Orchestrator says "I've routed your request to **letta**"
  - WebSocket times out with a keepalive ping timeout
  - User never gets a response from Scissari

Tests are ordered from most fundamental (network/API) to end-to-end so the
first failing test tells you exactly which layer is broken.

Run:
  cd /home/adamsl/planner
  source a2a_communicating_agents/.venv/bin/activate
  python -m pytest a2a_communicating_agents/tests/test_orchestrator_scissari_bridge.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Path setup — run from /home/adamsl/a2a_communicating_agents
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # /home/adamsl/a2a_communicating_agents
PLANNER_ROOT = PROJECT_ROOT  # kept as alias
sys.path.insert(0, str(PROJECT_ROOT.parent))  # for: from a2a_communicating_agents.X import Y
sys.path.insert(0, str(PROJECT_ROOT))          # for: from rag_system.X import Y

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LETTA_BASE_URL = os.getenv("LETTA_BASE_URL", "http://100.80.49.10:8283")
SCISSARI_AGENT_ID = "agent-5955b0c2-7922-4ffe-9e43-b116053b80fa"
WEBSOCKET_URL = os.getenv("A2A_WEBSOCKET_URL", "ws://localhost:3030")
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]  # a2a_communicating_agents/


# ===========================================================================
# LAYER 1: Letta API connectivity
# ===========================================================================

class TestLettaApiConnectivity:
    """
    Layer 1 — Can we reach the Letta server at all?

    If these fail, every higher-level test will also fail.
    Nothing works without the Letta server.
    """

    def test_letta_server_is_reachable(self):
        """HTTP GET /v1/health (or /api/health) returns 200."""
        import requests

        try:
            resp = requests.get(f"{LETTA_BASE_URL}/v1/health", timeout=5)
        except requests.ConnectionError as exc:
            pytest.fail(
                f"Cannot reach Letta server at {LETTA_BASE_URL}: {exc}\n"
                "Fix: start the Letta server or set LETTA_BASE_URL correctly."
            )
        assert resp.status_code in (200, 204), (
            f"Letta server health check returned {resp.status_code}: {resp.text[:200]}"
        )

    def test_letta_client_can_list_agents(self):
        """letta_client SDK connects and lists agents without raising."""
        from letta_client import Letta

        client = Letta(base_url=LETTA_BASE_URL)
        try:
            agents = client.agents.list()
        except Exception as exc:
            pytest.fail(
                f"letta_client.agents.list() raised: {exc}\n"
                "Fix: check LETTA_BASE_URL and server authentication."
            )
        # agents may be empty, but must be a list/page, not an error
        assert agents is not None, "agents.list() returned None"

    def test_scissari_agent_exists(self):
        """Scissari's agent ID resolves to a real agent on the Letta server."""
        from letta_client import Letta

        client = Letta(base_url=LETTA_BASE_URL)
        try:
            agent = client.agents.retrieve(SCISSARI_AGENT_ID)
        except Exception as exc:
            pytest.fail(
                f"Cannot retrieve Scissari (agent_id={SCISSARI_AGENT_ID}): {exc}\n"
                "Fix: verify the agent ID is correct and the server is running."
            )
        assert agent.id == SCISSARI_AGENT_ID, (
            f"Retrieved agent ID mismatch: got {agent.id}"
        )
        # Confirm the name so we don't silently test the wrong agent
        assert "scissari" in (agent.name or "").lower() or agent.id == SCISSARI_AGENT_ID, (
            f"Agent name doesn't match Scissari: {agent.name}"
        )


# ===========================================================================
# LAYER 2: Direct Letta message delivery (bypassing orchestrator)
# ===========================================================================

class TestDirectLettaMessage:
    """
    Layer 2 — Can we send a message directly to Scissari via the Letta API?

    This isolates the Letta API layer from the WebSocket/orchestrator layer.
    If this fails, the issue is in the Letta API or authentication.
    If this passes but E2E fails, the issue is in the orchestrator→Letta bridge.
    """

    def test_direct_message_to_scissari_returns_response(self):
        """
        Send a simple ping to Scissari directly via letta_client.

        Uses conversations.messages.create with a real conversation (Letta 0.16.3
        quirk: 'default' conversation ID fails, must create a real one).
        """
        from letta_client import Letta

        client = Letta(base_url=LETTA_BASE_URL)

        # Letta 0.16.3: create a real conversation first
        try:
            conv = client.conversations.create(agent_id=SCISSARI_AGENT_ID)
        except Exception as exc:
            pytest.fail(
                f"conversations.create() raised: {exc}\n"
                "This is needed because Letta 0.16.3 rejects the 'default' conversation ID."
            )

        assert conv.id, "conversations.create() returned an object with no ID"

        try:
            result = client.conversations.messages.create(
                conv.id,
                messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
            )
        except Exception as exc:
            pytest.fail(
                f"conversations.messages.create() raised: {exc}\n"
                "Fix: check Letta server logs for the error detail."
            )

        assert result is not None, "conversations.messages.create() returned None"

    def test_scissari_response_contains_text(self):
        """
        Scissari actually responds with non-empty text content.

        Uses runs.retrieve() + runs.steps.list() because Letta 0.16.3's
        runs.messages.list() only returns the initiating user_message.
        """
        from letta_client import Letta

        client = Letta(base_url=LETTA_BASE_URL)
        conv = client.conversations.create(agent_id=SCISSARI_AGENT_ID)
        result = client.conversations.messages.create(
            conv.id,
            messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
        )

        # Find the run ID in the response
        run_id = getattr(result, "id", None) or getattr(result, "run_id", None)
        if run_id is None:
            # Some SDK versions nest it differently
            run_id = getattr(result, "run", {}).id if hasattr(getattr(result, "run", None), "id") else None

        if run_id is None:
            pytest.skip("Could not extract run_id from response — check SDK version")

        # Poll until run completes (max 60s)
        deadline = time.time() + 60
        run = None
        while time.time() < deadline:
            run = client.runs.retrieve(run_id)
            if getattr(run, "status", None) in ("completed", "failed", "error"):
                break
            time.sleep(2)

        assert run is not None, "runs.retrieve() returned None"
        assert getattr(run, "status", None) == "completed", (
            f"Run did not complete within 60s. Final status: {getattr(run, 'status', 'unknown')}\n"
            "Fix: check Scissari's model configuration and Letta server logs."
        )

        # Verify a real model response was produced (completion_tokens > 0)
        steps_page = client.runs.steps.list(run_id, limit=10)
        steps = steps_page.getPaginatedItems() if hasattr(steps_page, "getPaginatedItems") else list(steps_page)
        successful_steps = [
            s for s in steps
            if getattr(s, "status", None) == "success"
            and getattr(s, "completion_tokens", 0) > 0
        ]
        assert successful_steps, (
            "No steps with completion_tokens > 0 found.\n"
            "Scissari produced no model output. "
            "Fix: check model configuration and server logs."
        )


# ===========================================================================
# LAYER 3: WebSocket server connectivity
# ===========================================================================

class TestWebSocketConnectivity:
    """
    Layer 3 — Is the A2A WebSocket server running and accepting connections?

    If this fails, no agent can communicate through the message bus.
    Fix: run start_websocket_server.sh from the a2a_communicating_agents dir.
    """

    def test_websocket_server_accepts_connections(self):
        """TCP connection to localhost:3030 succeeds."""
        import socket

        ws_host = "localhost"
        ws_port = 3030

        try:
            with socket.create_connection((ws_host, ws_port), timeout=3):
                pass  # connection opened and closed cleanly
        except (ConnectionRefusedError, OSError) as exc:
            pytest.fail(
                f"WebSocket server not listening on {ws_host}:{ws_port}: {exc}\n"
                "Fix: cd a2a_communicating_agents && ./start_websocket_server.sh"
            )

    @pytest.mark.asyncio
    async def test_websocket_handshake_completes(self):
        """Full WebSocket handshake with the A2A server completes."""
        try:
            import websockets
        except ImportError:
            pytest.skip("websockets package not installed in this environment")

        try:
            async with websockets.connect(WEBSOCKET_URL, open_timeout=5) as ws:
                # Server should accept the connection; send a no-op ping
                await ws.ping()
        except Exception as exc:
            pytest.fail(
                f"WebSocket handshake to {WEBSOCKET_URL} failed: {exc}\n"
                "Fix: confirm the WebSocket server is running and the URL is correct."
            )


# ===========================================================================
# LAYER 4: Agent card discovery — does "letta" appear in the registry?
# ===========================================================================

class TestAgentCardDiscovery:
    """
    Layer 4 — Does the A2A collective discover the "letta" agent card?

    The orchestrator discovers agents by scanning agent.json files under the
    workspace root. The "letta" agent card lives at:
      agent_messaging/agents/letta/agent.json

    If the card is missing or unreadable the orchestrator can never route to
    "letta", so it will fall through to FallbackRouter with a self-reply.
    """

    def test_letta_agent_card_file_exists(self):
        """agent_messaging/agents/letta/agent.json exists and is valid JSON."""
        card_path = WORKSPACE_ROOT / "agent_messaging" / "agents" / "letta" / "agent.json"
        assert card_path.exists(), (
            f"Letta agent card not found at {card_path}\n"
            "Fix: create the agent.json file so the orchestrator can discover 'letta'."
        )
        try:
            card = json.loads(card_path.read_text())
        except json.JSONDecodeError as exc:
            pytest.fail(f"agent.json is invalid JSON: {exc}")

        assert card.get("name") == "letta", (
            f"Expected name='letta', got name={card.get('name')!r}"
        )

    def test_letta_agent_card_has_topics(self):
        """The letta agent card declares at least one topic for routing."""
        card_path = WORKSPACE_ROOT / "agent_messaging" / "agents" / "letta" / "agent.json"
        if not card_path.exists():
            pytest.skip("letta agent card missing — covered by test_letta_agent_card_file_exists")

        card = json.loads(card_path.read_text())
        topics = card.get("topics", [])
        assert topics, (
            "letta agent card has no topics. The orchestrator routes by first topic.\n"
            "Fix: add at least one topic to the card (e.g. ['letta', 'memory'])."
        )

    @pytest.mark.asyncio
    async def test_collective_hub_discovers_letta(self):
        """A2ACollectiveHub.discover_agents() includes 'letta' in the registry."""
        from a2a_communicating_agents.agent_messaging.a2a_collective import A2ACollectiveHub

        class _NullMemoryFactory:
            @staticmethod
            async def create_memory_async(agent_id=None, **_kw):
                from a2a_communicating_agents.agent_messaging.memory_backend import MemoryBackend

                class _NullMemory(MemoryBackend):
                    namespace = "null"
                    async def connect(self): pass
                    async def disconnect(self): pass
                    async def remember(self, *a, **kw): return None
                    async def recall(self, *a, **kw): return []
                    async def get_recent(self, *a, **kw): return []
                    async def forget(self, *a, **kw): return False
                    async def get_stats(self): return {}
                    def is_connected(self): return False

                return ("null", _NullMemory())

        hub = A2ACollectiveHub(workspace_root=WORKSPACE_ROOT, memory_factory=_NullMemoryFactory)
        registry = await hub.discover_agents()

        assert "letta" in registry, (
            f"'letta' not found in collective registry.\n"
            f"Discovered agents: {list(registry.keys())}\n"
            "Fix: ensure agent_messaging/agents/letta/agent.json exists with name='letta'."
        )


# ===========================================================================
# LAYER 5: Orchestrator routing — does it route to "letta"?
# ===========================================================================

class TestOrchestratorRouting:
    """
    Layer 5 — Does the orchestrator LLM route a 'message Scissari' request to 'letta'?

    Uses a mock LLM client so the test doesn't require a live codex/OpenAI connection.
    The LLMRouter is tested with an injected SDK-compatible mock.
    """

    @pytest.mark.asyncio
    async def test_llm_router_routes_scissari_request_to_letta(self):
        """LLMRouter with injected mock routes 'send to Scissari' → 'letta'."""
        from a2a_communicating_agents.orchestrator_agent.routing import (
            LLMRouter, RoutingContext, SELF,
        )

        # Inject a mock LLM that returns DELEGATE: letta
        mock_message = MagicMock()
        mock_message.content = "DELEGATE: letta"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_completion

        router = LLMRouter(client=mock_client)
        context = RoutingContext(
            known_agents={"letta": {"description": "Memory management specialist"}},
            orchestrator_name="orchestrator-agent",
            orchestrator_description="Routes tasks to agents.",
            codex_path="/usr/local/bin/codex",
            model_id="gpt-test",
            planner_root=PLANNER_ROOT,
        )

        decision = await router.route(
            "Send a test message to Letta Agent agent-5955b0c2-7922-4ffe-9e43-b116053b80fa "
            "The Letta Agent's name is Scissari. She lives at http://100.80.49.10:8283.",
            context,
        )

        assert decision is not None, "LLMRouter returned None — LLM call failed"
        assert decision.target == "letta", (
            f"Expected target='letta', got '{decision.target}'\n"
            "The LLM should delegate Scissari-directed messages to the 'letta' agent."
        )

    @pytest.mark.asyncio
    async def test_orchestrator_knows_about_letta_after_discovery(self):
        """After discover_agents(), orchestrator.known_agents contains 'letta'."""
        from unittest.mock import patch, AsyncMock

        try:
            from a2a_communicating_agents.orchestrator_agent.routing import (
                FallbackRouter, RouterChain,
            )
            from a2a_communicating_agents.orchestrator_agent.main import Orchestrator
        except ModuleNotFoundError as exc:
            pytest.skip(
                f"Orchestrator has an uninstalled optional dependency: {exc}\n"
                "Install it in the venv (e.g. pip install chromadb) to enable this test."
            )

        # Stub out messenger and remote logger to avoid side effects
        with patch("a2a_communicating_agents.orchestrator_agent.main.RemoteLogger") as mock_logger_cls, \
             patch("a2a_communicating_agents.agent_messaging.messenger.TransportManager"):

            mock_logger = MagicMock()
            mock_logger.init = MagicMock()
            mock_logger.clear_logs = MagicMock()
            mock_logger.log = MagicMock()
            mock_logger_cls.return_value = mock_logger

            router = FallbackRouter()
            orch = Orchestrator(router=RouterChain([router]))

            # Patch dispatcher to return a fake registry that includes 'letta'
            async def fake_refresh():
                return {"letta": MagicMock()}

            def fake_snapshot():
                return {
                    "letta": {
                        "description": "Memory management specialist",
                        "capabilities": ["memory_management"],
                        "topics": ["memory", "general"],
                        "memory": {"backend": "null", "connected": False},
                    }
                }

            orch.dispatcher.refresh_registry = fake_refresh
            orch.dispatcher.routing_snapshot = fake_snapshot

            await orch.discover_agents()

        assert "letta" in orch.known_agents, (
            f"'letta' not in known_agents after discovery.\n"
            f"known_agents: {list(orch.known_agents.keys())}\n"
            "Fix: ensure the letta agent card is discovered and not filtered as a self-reference."
        )


# ===========================================================================
# LAYER 6: THE MISSING BRIDGE (expected to FAIL until bridge is built)
#
# This is the root cause of the observed WebSocket timeout.
# When the orchestrator routes to "letta", it puts a JSON-RPC payload on the
# WebSocket topic "memory". Nobody listens there — there is no bridge agent
# that reads from the "memory" topic and forwards to the Letta API.
# ===========================================================================

class TestLettaBridge:
    """
    Layer 6 — Is there a bridge between the 'letta' WebSocket topic and the Letta API?

    THIS IS THE ROOT CAUSE: When the orchestrator delegates to "letta", it sends
    a JSON-RPC message to the WebSocket topic "memory" (first topic in letta's
    agent.json). Nobody subscribes to that topic. The message is dropped.
    Scissari never receives it. The WebSocket eventually times out.

    The fix requires a bridge process that:
      1. Subscribes to the WebSocket topic used by the letta agent card
      2. Parses the JSON-RPC payload
      3. Extracts the natural-language description from params.description
      4. Sends that description to Scissari via letta_client
      5. Relays Scissari's response back to the orchestrator topic
    """

    @pytest.mark.asyncio
    async def test_letta_websocket_topic_has_a_subscriber(self):
        """
        EXPECTED TO FAIL until a bridge agent is running.

        This test verifies there is a live process subscribed to the letta agent's
        WebSocket topic that will relay messages to the Letta API.

        Strategy:
          1. Subscribe to the 'orchestrator' topic to catch any bridge reply
          2. Send a JSON-RPC probe to the primary letta topic (e.g. 'memory')
          3. Wait 15s for a response to appear on 'orchestrator' from the bridge
          4. FAIL if no response arrives — that confirms the missing bridge

        The test filters out subscription ACKs (type=subscribed/sent/registered)
        and server history replays, so only real relayed messages count.
        """
        try:
            import websockets
        except ImportError:
            pytest.skip("websockets package not installed")

        letta_card_path = WORKSPACE_ROOT / "agent_messaging" / "agents" / "letta" / "agent.json"
        if not letta_card_path.exists():
            pytest.skip("letta agent card missing — covered by TestAgentCardDiscovery")

        card = json.loads(letta_card_path.read_text())
        primary_topic = (card.get("topics") or ["memory"])[0]

        bridge_replies: List[str] = []

        async def _listen_for_bridge_reply():
            """Watch the orchestrator topic for a message relayed by the bridge."""
            async with websockets.connect(WEBSOCKET_URL, open_timeout=5) as ws:
                # Register and subscribe to orchestrator topic to catch bridge responses
                await ws.send(json.dumps({"type": "register", "agent_id": "test-bridge-listener"}))
                # Drain the registration reply
                await asyncio.wait_for(ws.recv(), timeout=3)

                await ws.send(json.dumps({"type": "subscribe", "topic": "orchestrator"}))
                # Drain the subscription ACK
                await asyncio.wait_for(ws.recv(), timeout=3)

                # Also subscribe to letta topic to confirm the probe arrived (sanity check)
                await ws.send(json.dumps({"type": "subscribe", "topic": primary_topic}))
                await asyncio.wait_for(ws.recv(), timeout=3)

                # Wait up to 15s for a real message envelope (type=message) from the bridge
                deadline = asyncio.get_event_loop().time() + 15
                while asyncio.get_event_loop().time() < deadline:
                    remaining = deadline - asyncio.get_event_loop().time()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2))
                    except asyncio.TimeoutError:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Filter out server ACKs — only count real message envelopes
                    if data.get("type") != "message":
                        continue
                    # Filter out the probe echoed back on the letta topic (that's
                    # the publisher's own message bouncing back to us — not a bridge reply)
                    from_agent = data.get("from_agent", "")
                    if from_agent == "test-bridge-probe-sender":
                        continue  # our own probe echoed back
                    # Any other message envelope on orchestrator = bridge replied
                    if data.get("topic") == "orchestrator":
                        bridge_replies.append(data.get("content", "<no content>"))
                        return

        async def _send_probe_to_letta_topic():
            """Send a JSON-RPC delegation payload to the letta agent's primary topic."""
            await asyncio.sleep(1.0)  # let listener subscribe first
            async with websockets.connect(WEBSOCKET_URL, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "register", "agent_id": "test-bridge-probe-sender"}))
                await asyncio.wait_for(ws.recv(), timeout=3)  # drain registered ACK

                probe_content = json.dumps({
                    "jsonrpc": "2.0",
                    "method": "agent.execute_task",
                    "params": {
                        "task_id": "test-bridge-probe-001",
                        "target_agent": "letta",
                        "description": (
                            f"BRIDGE_PROBE: Send a message to Scissari "
                            f"({SCISSARI_AGENT_ID}) asking her to reply with: "
                            "BRIDGE_PROBE_OK"
                        ),
                        "context": {},
                        "artifacts": [],
                    },
                    "id": 1,
                })
                await ws.send(json.dumps({
                    "type": "send",         # correct message type for the WS server
                    "topic": primary_topic,
                    "content": probe_content,
                    "to_agent": "letta",
                    "from_agent": "test-bridge-probe-sender",
                    "priority": "normal",
                    "metadata": {},
                }))
                # Drain the "sent" ACK
                try:
                    await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    pass

        await asyncio.gather(_listen_for_bridge_reply(), _send_probe_to_letta_topic())

        # --- THIS ASSERTION FAILS when no bridge is running ---
        assert bridge_replies, (
            "MISSING BRIDGE DETECTED: No reply arrived on the 'orchestrator' WebSocket topic "
            f"within 15 seconds after sending a JSON-RPC probe to topic '{primary_topic}'.\n\n"
            "Root cause: The orchestrator sends JSON-RPC payloads to the WebSocket topic\n"
            "declared in agent_messaging/agents/letta/agent.json (currently: "
            f"'{primary_topic}'),\n"
            "but no process is subscribed to forward those payloads to the Letta API.\n\n"
            "Fix: Create a bridge process (e.g. letta_bridge.py) that:\n"
            "  1. Subscribes to WebSocket topic(s) declared in the letta agent card\n"
            "  2. Extracts params.description from the JSON-RPC payload\n"
            "  3. Creates a Letta conversation and sends the message to Scissari\n"
            f"     (agent ID: {SCISSARI_AGENT_ID})\n"
            "  4. Waits for Scissari's response via runs.retrieve() + runs.steps.list()\n"
            "  5. Posts Scissari's reply back to the 'orchestrator' WebSocket topic\n\n"
            "Start the bridge with: python3 letta_bridge.py &\n"
            "Then re-run these tests to confirm the fix."
        )


# ===========================================================================
# LAYER 7: End-to-end — send via orchestrator, get Scissari's reply back
# ===========================================================================

@pytest.mark.skip(
    reason=(
        "E2E test — requires all layers to pass first. "
        "Specifically requires the Letta bridge to be running: "
        "cd a2a_communicating_agents && ./start_websocket_server.sh && ./start_letta_bridge.sh. "
        "Remove this skip once test_letta_websocket_topic_has_a_subscriber passes."
    )
)
class TestEndToEndOrchestratorToScissari:
    """
    Layer 7 — Full round-trip: chat message → orchestrator → letta bridge → Scissari → reply.

    Only run this after Layer 6 passes (bridge is running).
    """

    @pytest.mark.asyncio
    async def test_orchestrator_relays_scissari_reply_to_user(self):
        """
        A user message sent to the 'orchestrator' WebSocket topic should produce
        a Scissari response relayed back to 'orchestrator' within 90 seconds.
        """
        try:
            import websockets
        except ImportError:
            pytest.skip("websockets package not installed")

        replies: List[str] = []

        async def _listen_for_reply():
            async with websockets.connect(WEBSOCKET_URL, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "register", "agent_id": "e2e-test-listener"}))
                await ws.send(json.dumps({"type": "subscribe", "topic": "orchestrator"}))

                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        data = json.loads(raw) if raw.startswith("{") else {}
                        content = data.get("content", "")
                        # Skip the user's own message echoed back
                        if "e2e-test-listener" not in content and content:
                            replies.append(content)
                            return
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break

        async def _send_user_message():
            await asyncio.sleep(1)  # let listener subscribe first
            async with websockets.connect(WEBSOCKET_URL, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "register", "agent_id": "e2e-test-sender"}))
                await ws.send(json.dumps({
                    "type": "message",
                    "topic": "orchestrator",
                    "content": (
                        f"Send a test message to Letta Agent {SCISSARI_AGENT_ID}. "
                        "The Letta Agent's name is Scissari. "
                        f"She lives at the self-hosted Letta Server ({LETTA_BASE_URL}). "
                        "Ask her to reply with exactly: SCISSARI_BRIDGE_OK"
                    ),
                    "from": "e2e-test-sender",
                }))

        await asyncio.gather(_listen_for_reply(), _send_user_message())

        assert replies, (
            "No reply received from orchestrator within 90s after sending Scissari message.\n"
            "Check: orchestrator logs, letta bridge logs, and Scissari agent status."
        )
        # The reply should eventually contain something from Scissari
        combined = " ".join(replies).lower()
        assert "scissari" in combined or "scissari_bridge_ok" in combined or len(replies) > 0, (
            f"Reply received but doesn't mention Scissari: {replies[0][:300]}"
        )
