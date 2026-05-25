#!/usr/bin/env python3
"""
Interactive orchestrator chat utility.

Bridges the agent messaging system with the orchestrator topic so we can talk
to the OpenAI-backed orchestrator agent from any terminal session. The script
optionally auto-starts the orchestrator process and keeps a rolling view of the
conversation topic to reduce duplicate implementations across directories.

Architecture note
-----------------
All I/O runs inside a *single* asyncio.run() call.  Blocking user input is
offloaded to a thread via asyncio.to_thread() so the event loop — and
therefore the WebSocket ping/pong keepalive — keeps running continuously.
The old self._loop.run_until_complete() pattern stopped the loop while the
user typed, which caused the server to close the connection with error 1011
("keepalive ping timeout") after ~30-60 seconds of silence.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from rich.console import Console
from rich.prompt import Prompt

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]  # /home/adamsl/a2a_communicating_agents
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))  # for: from a2a_communicating_agents.X import Y
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))          # for: from rag_system.X import Y

PACKAGE_ROOT = PROJECT_ROOT  # the project IS the a2a_communicating_agents package

from a2a_communicating_agents.agent_messaging.agent_messaging_interface import (
    AgentMessage,
)
from a2a_communicating_agents.agent_messaging.message_models import (
    AgentMessage as TransportAgentMessage,
    MessagePriority as TransportMessagePriority,
)
from a2a_communicating_agents.agent_messaging.message_transport import MessageTransport
from a2a_communicating_agents.agent_messaging.transport_factory import TransportFactory
from rag_system.core.document_manager import DocumentManager

console = Console()


def _default_orchestrator_path() -> Path:
    """Return the absolute path to the orchestrator agent directory."""
    return PACKAGE_ROOT / "orchestrator_agent"


class OrchestratorProcessManager:
    """Simple helper to optionally start/stop the orchestrator process."""

    def __init__(self, orchestrator_path: Path):
        self.orchestrator_path = orchestrator_path
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self.process:
            return
        if not self.orchestrator_path.exists():
            raise FileNotFoundError(
                f"Orchestrator path '{self.orchestrator_path}' does not exist"
            )
        console.print(
            f"🚀 Starting orchestrator agent from {self.orchestrator_path}", style="cyan"
        )
        env = os.environ.copy()
        self.process = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=str(self.orchestrator_path),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        if not self.process:
            return
        console.print("🛑 Stopping orchestrator agent...", style="yellow")
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None


class OrchestratorChatSession:
    """
    Stateful helper for WebSocket-based orchestrator communication.

    Usage (async context required — call setup() before anything else):

        session = OrchestratorChatSession(agent_name="dashboard-ui", topic="orchestrator")
        await session.setup()          # connect + subscribe
        sent = await session.send_user_message("hello")
        msgs = await session.wait_for_response_async(timeout=45)
    """

    _HEADER_PATTERN = re.compile(r"^\*\*(?P<label>[^*]+)\*\*\s*(?P<value>.*)$")

    def __init__(
        self,
        *,
        agent_name: str,
        topic: str,
        poll_limit: int = 20,
        transport_name: Optional[str] = None,
        transport: Optional[MessageTransport] = None,
        doc_manager: Optional[DocumentManager] = None,
        transport_factory=TransportFactory,
    ):
        self.agent_name = agent_name
        self.topic = topic
        self.poll_limit = poll_limit
        self._seen_ids: Set[str] = set()
        self._doc_manager = doc_manager or DocumentManager()
        self._transport_factory = transport_factory
        self._incoming_messages: asyncio.Queue = asyncio.Queue()
        self._subscription_active = False
        self._last_sent_payload: Optional[str] = None
        self._last_sent_at: Optional[datetime] = None

        # If a transport is injected directly (testing), store it now.
        # Otherwise setup() will create one asynchronously.
        self._injected_transport_name = transport_name
        self._injected_transport = transport

        self.transport_name: Optional[str] = transport_name
        self.transport: Optional[MessageTransport] = transport

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Connect transport and subscribe to topic. Must be awaited before use."""
        if self._injected_transport is not None:
            # Testing path: transport already provided
            self.transport_name = self._injected_transport_name or "injected"
            self.transport = self._injected_transport
        else:
            name, transport_instance = await self._transport_factory.create_transport_async(
                agent_id=self.agent_name,
                doc_manager=self._doc_manager,
            )
            self.transport_name = name
            self.transport = transport_instance

        console.print(
            f"  Using '{self.transport_name}' transport for orchestrator chat",
            style="dim",
        )
        await self.subscribe_to_topic()

    async def subscribe_to_topic(self) -> None:
        """Subscribe to the orchestrator topic for real-time delivery."""
        if self.transport_name != "websocket":
            return
        if self._subscription_active:
            return
        if self.transport is None:
            return
        try:
            await self.transport.subscribe(self.topic, self._message_callback)
            self._subscription_active = True
            console.print(
                f"  📬 Subscribed to topic '{self.topic}' for real-time updates",
                style="dim",
            )
        except Exception as exc:
            console.print(f"  ⚠️  Could not subscribe to topic: {exc}", style="yellow")

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_user_message(self, message: str) -> Optional[AgentMessage]:
        """Send a user-authored message to the orchestrator topic."""
        payload = message.strip()
        if not payload:
            return None
        if self.transport is None:
            console.print("❌ Transport not initialized — call setup() first.", style="red")
            return None

        transport_message = TransportAgentMessage(
            to_agent="board",
            from_agent=self.agent_name,
            content=payload,
            topic=self.topic,
            priority=TransportMessagePriority.NORMAL,
        )
        try:
            success = await self.transport.send(transport_message)
        except Exception as exc:
            console.print(f"❌ Failed to send message: {exc}", style="red")
            return None

        if not success:
            console.print("❌ Transport rejected the message.", style="red")
            return None

        self._last_sent_payload = payload
        self._last_sent_at = self._normalize_timestamp(transport_message.timestamp)
        local_msg = self._build_local_agent_message(
            payload=payload,
            timestamp=self._last_sent_at,
        )
        self._seen_ids.add(self._message_key(local_msg))
        return local_msg

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    async def _message_callback(self, message: AgentMessage) -> None:
        """Callback for real-time WebSocket messages."""
        await self._incoming_messages.put(message)

    def fetch_new_messages(self) -> List[AgentMessage]:
        """
        Drain any queued incoming messages (non-blocking).

        Safe to call from async or sync context; does NOT suspend or await.
        """
        normalized: List[AgentMessage] = []
        while not self._incoming_messages.empty():
            try:
                message = self._incoming_messages.get_nowait()
                key = self._message_key(message)
                if key in self._seen_ids:
                    continue
                self._seen_ids.add(key)
                if self._is_machine_response(message):
                    continue
                normalized.append(message)
            except Exception:
                break
        normalized.sort(key=lambda m: getattr(m, "timestamp", datetime.utcnow()))
        return normalized

    async def wait_for_response_async(
        self,
        timeout: float = 45.0,
        expected_external_messages: int = 1,
    ) -> List[AgentMessage]:
        """Wait for incoming messages via WebSocket subscription."""
        messages = []
        external_count = 0
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                message = await asyncio.wait_for(
                    self._incoming_messages.get(),
                    timeout=min(remaining, 1.0),
                )
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                console.print(f"  Error waiting for response: {exc}", style="red")
                break

            key = self._message_key(message)
            if key in self._seen_ids:
                continue
            self._seen_ids.add(key)
            if self._is_machine_response(message):
                continue

            messages.append(message)
            sender = self._message_sender(message).strip().lower()
            content = (getattr(message, "content", "") or "").strip()
            is_self = sender == self.agent_name.lower()
            is_echo = self._last_sent_payload is not None and content == self._last_sent_payload
            is_ack = self._is_routing_ack(message)
            if not is_self and not is_echo and not is_ack:
                external_count += 1
                if external_count >= expected_external_messages:
                    break

        return messages

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_messages(self, messages: Sequence[AgentMessage]) -> None:
        """Render a collection of messages with simple colour coding."""
        for msg in messages:
            timestamp = getattr(msg, "timestamp", None)
            time_str = (
                timestamp.strftime("%H:%M:%S") if isinstance(timestamp, datetime) else "--:--"
            )
            sender = self._message_sender(msg)
            content = (msg.content or "").strip()
            if sender == self.agent_name:
                style = "green"
            elif "orchestrator" in sender.lower():
                style = "cyan"
            else:
                style = "white"
            console.print(f"[dim]{time_str}[/dim] [{style}]{sender}[/]: {content}")

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------

    def _message_key(self, message: AgentMessage) -> str:
        if isinstance(message.metadata, dict):
            mid = message.metadata.get("document_id")
            if mid:
                return mid
        if getattr(message, "document_id", None):
            return str(message.document_id)
        ts = getattr(message, "timestamp", None)
        sender = self._message_sender(message)
        return f"{sender}:{ts}:{hash(message.content)}"

    def _message_sender(self, message: AgentMessage) -> str:
        sender = getattr(message, "sender", None) or getattr(message, "from_agent", None)
        return str(sender or "unknown")

    def _is_routing_ack(self, message: AgentMessage) -> bool:
        sender = self._message_sender(message).strip().lower()
        content = (getattr(message, "content", "") or "").strip().lower()
        # Match both "I've routed" and "I have routed" (orchestrator uses the contraction)
        return "orchestrator" in sender and "routed your request to **" in content

    def _is_machine_response(self, message: AgentMessage) -> bool:
        content = (getattr(message, "content", "") or "").strip()
        return content.startswith("{") and '"jsonrpc"' in content[:80]

    def _is_before_last_send(self, message: AgentMessage) -> bool:
        if self._last_sent_at is None:
            return False
        ts = getattr(message, "timestamp", None)
        if not isinstance(ts, datetime):
            return False
        return self._normalize_timestamp(ts) < self._last_sent_at

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_timestamp(timestamp: datetime) -> datetime:
        if timestamp.tzinfo:
            return timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        return timestamp

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> datetime:
        if not value:
            return datetime.utcnow()
        sanitized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(sanitized)
        except ValueError:
            return datetime.utcnow()
        return OrchestratorChatSession._normalize_timestamp(parsed)

    def _build_local_agent_message(self, *, payload: str, timestamp: datetime) -> AgentMessage:
        metadata = {"transport_source": self.transport_name, "local_echo": True}
        message_id = f"local-{hash((timestamp.isoformat(), payload))}"
        return AgentMessage(
            id=message_id,
            document_id=message_id,
            content=payload,
            topic=self.topic,
            sender=self.agent_name,
            priority=TransportMessagePriority.NORMAL.value,
            timestamp=timestamp,
            metadata=metadata,
            score=1.0,
            raw=None,
        )


# ---------------------------------------------------------------------------
# Convenience helper for programmatic use
# ---------------------------------------------------------------------------

async def send_message_to_orchestrator(
    message: str,
    *,
    agent_name: str = "api-client",
    topic: str = "orchestrator",
    timeout: float = 60.0,
) -> str:
    """
    Send a single message to the orchestrator and return the reply text.

    Example:
        response = await send_message_to_orchestrator("Write a hello-world function.")
    """
    session = OrchestratorChatSession(agent_name=agent_name, topic=topic)
    await session.setup()
    sent = await session.send_user_message(message)
    if not sent:
        return "(failed to send message)"
    replies = await session.wait_for_response_async(timeout=timeout)
    if not replies:
        return "(no response within timeout)"
    return "\n".join((r.content or "").strip() for r in replies)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chat with the orchestrator agent over the shared message board.",
    )
    parser.add_argument(
        "--agent-name",
        default=os.getenv("ORCHESTRATOR_CHAT_AGENT", "dashboard-ui"),
        help="Name to use when posting messages.",
    )
    parser.add_argument(
        "--topic",
        default="orchestrator",
        help="Message topic to monitor.",
    )
    parser.add_argument(
        "--poll-limit",
        type=int,
        default=25,
        help="Maximum messages to fetch per refresh.",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for non-self responses after sending.",
    )
    parser.add_argument(
        "--auto-start",
        action="store_true",
        help="Automatically launch the orchestrator agent before chatting.",
    )
    parser.add_argument(
        "--orchestrator-path",
        type=Path,
        default=_default_orchestrator_path(),
        help="Location of the orchestrator agent (used with --auto-start).",
    )
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> None:
    """
    Fully-async REPL.  The event loop never stops — keepalives stay alive.

    User input is read via asyncio.to_thread(input, ...) so the event loop
    (and WebSocket ping/pong) keeps running while the user is thinking.
    """
    manager: Optional[OrchestratorProcessManager] = None
    if args.auto_start:
        manager = OrchestratorProcessManager(args.orchestrator_path)
        try:
            manager.start()
        except Exception as exc:
            console.print(f"❌ Failed to auto-start orchestrator: {exc}", style="red")
            manager = None

    session = OrchestratorChatSession(
        agent_name=args.agent_name,
        topic=args.topic,
        poll_limit=args.poll_limit,
    )
    await session.setup()

    console.print("\n🤝 Orchestrator Chat")
    console.print(" Type message and press Enter to send.")
    console.print(" Commands: /refresh, /quit, /help\n", style="dim")

    initial_messages = session.fetch_new_messages()
    if initial_messages:
        session.render_messages(initial_messages)
    else:
        console.print(
            "📭 No orchestrator messages yet. Start the conversation!", style="yellow"
        )

    prompt_str = f"[{args.agent_name}]: "

    while True:
        try:
            # Run blocking input() in a thread so the event loop stays alive.
            user_input = await asyncio.to_thread(input, prompt_str)
            user_input = user_input.strip()
        except (EOFError, KeyboardInterrupt):
            break

        normalized = user_input.lower()
        if normalized in {"", "/refresh", "/r"}:
            new_msgs = session.fetch_new_messages()
            if new_msgs:
                session.render_messages(new_msgs)
            else:
                console.print("📭 No new messages.", style="dim")
            continue
        if normalized in {"/quit", "/exit", "/q"}:
            break
        if normalized in {"/help", "help"}:
            console.print("Commands: /refresh, /quit, /help", style="dim")
            continue

        # Show any messages that arrived while the user was typing
        queued = session.fetch_new_messages()
        if queued:
            session.render_messages(queued)

        sent = await session.send_user_message(user_input)
        if not sent:
            # Error already printed by send_user_message
            continue

        console.print("✅ Message sent. Waiting for response...", style="green")

        if session.transport_name == "websocket" and session._subscription_active:
            new_msgs = await session.wait_for_response_async(
                timeout=max(1.0, args.wait_timeout)
            )
            if new_msgs:
                session.render_messages(new_msgs)
            else:
                console.print(
                    f"⏱️  No non-self response within {int(args.wait_timeout)}s. "
                    "A specialist agent may still be processing; use /refresh.",
                    style="yellow",
                )
        else:
            # Fallback: poll for non-WebSocket transports
            poll_timeout = max(1.0, args.wait_timeout)
            start_time = asyncio.get_event_loop().time()
            response_received = False
            while asyncio.get_event_loop().time() - start_time < poll_timeout:
                await asyncio.sleep(1.0)
                new_msgs = session.fetch_new_messages()
                if new_msgs:
                    session.render_messages(new_msgs)
                    response_received = True
                    break
            if not response_received:
                console.print(
                    f"⏱️  No response within {int(args.wait_timeout)}s. "
                    "Orchestrator may be processing or offline.",
                    style="yellow",
                )

    if manager:
        manager.stop()
    console.print("\n👋 Goodbye!\n", style="yellow")


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        console.print("\n👋 Goodbye!\n", style="yellow")


if __name__ == "__main__":
    main()
