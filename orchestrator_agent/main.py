#!/usr/bin/env python3
"""
Orchestrator Agent

Single Responsibility: receive messages on the 'orchestrator' WebSocket topic,
ask the RouterChain what to do with each one, then either send a direct reply
or delegate to a specialist agent via the dispatcher.

GoF Patterns in use:
- Observer      : AgentMessenger subscription delivers messages via callback
- Facade        : AgentMessenger hides WebSocket transport complexity
- Singleton     : TransportManager shares the WebSocket connection
- Chain of Resp.: RouterChain (routing/) tries each router in order
- Strategy      : IRouter implementations are interchangeable routing strategies
"""

import sys
import os
import time
import json
import asyncio
import shutil
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set

from datetime import datetime

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # /home/adamsl/a2a_communicating_agents
PLANNER_ROOT = PROJECT_ROOT  # kept as alias for internal references

from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)

sys.path.insert(0, str(PROJECT_ROOT.parent))  # for: from a2a_communicating_agents.X import Y
sys.path.insert(0, str(PROJECT_ROOT))          # for: from rag_system.X import Y
os.chdir(PROJECT_ROOT)

from a2a_communicating_agents.agent_messaging import (
    AgentMessenger,
    create_jsonrpc_response,
    AgentMessage,
)
try:
    from rag_system.core.document_manager import DocumentManager
except ModuleNotFoundError as exc:
    if exc.name == "chromadb":
        DocumentManager = None
    else:
        raise
from a2a_communicating_agents.orchestrator_agent.a2a_dispatcher import A2ADispatcher
from a2a_communicating_agents.orchestrator_agent.remote_logger import RemoteLogger
from a2a_communicating_agents.orchestrator_agent.routing import (
    IRouter,
    RouteDecision,
    RoutingContext,
    SELF,
    LLMRouter,
    FallbackRouter,
    RouterChain,
)

_REPORT_KEYWORDS = (
    "report",
    "reports",
    "write report",
    "write reports",
    "report writing",
    "draft report",
    "draft reports",
    "generate report",
    "generate reports",
)

AGENT_NAME = "orchestrator-agent"
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_LOGGER_ID = os.getenv("ORCHESTRATOR_LOGGER_ID", f"OrchestratorAgent_{time.localtime().tm_year}")
_REMOTE_LOGGER = None
_REMOTE_LOGGER_RETRY_AFTER = 0.0  # monotonic time; 0 = enabled
_REMOTE_LOGGER_FAILURE_REPORTED = False
_REMOTE_LOGGER_BACKOFF_SEC = 60.0


def set_remote_logger(logger):
    global _REMOTE_LOGGER
    _REMOTE_LOGGER = logger


def log_update(message):
    global _REMOTE_LOGGER_RETRY_AFTER, _REMOTE_LOGGER_FAILURE_REPORTED
    print(f"[{AGENT_NAME}] {message}")
    if _REMOTE_LOGGER is None:
        return
    if time.monotonic() < _REMOTE_LOGGER_RETRY_AFTER:
        return
    try:
        _REMOTE_LOGGER.log(message)
        if _REMOTE_LOGGER_FAILURE_REPORTED:
            _REMOTE_LOGGER_FAILURE_REPORTED = False
            print(f"[{AGENT_NAME}] Remote logger recovered.")
    except Exception as exc:
        _REMOTE_LOGGER_RETRY_AFTER = time.monotonic() + _REMOTE_LOGGER_BACKOFF_SEC
        if not _REMOTE_LOGGER_FAILURE_REPORTED:
            _REMOTE_LOGGER_FAILURE_REPORTED = True
            print(f"[{AGENT_NAME}] Remote logger error (retry in {int(_REMOTE_LOGGER_BACKOFF_SEC)}s): {exc}")


class Orchestrator:
    """
    Responsibilities:
      1. WebSocket lifecycle (subscribe, health check, reconnect)
      2. Message deduplication and loop-prevention (ignore sub-agent senders)
      3. Agent discovery (populates known_agents)
      4. Invoke RouterChain on each incoming message
      5. Act on RouteDecision: send direct reply or delegate to specialist
    """

    def __init__(self, *, router: Optional[IRouter] = None, llm_client=None, model_id: Optional[str] = None):
        self.known_agents: Dict[str, Any] = {}
        self.logger = RemoteLogger(ORCHESTRATOR_LOGGER_ID)
        try:
            self.logger.init()
        except Exception as exc:
            print(f"[{AGENT_NAME}] Remote logger init failed: {exc}")
        else:
            set_remote_logger(self.logger)
            try:
                self.logger.clear_logs("booting orchestrator.")
            except Exception as exc:
                print(f"[{AGENT_NAME}] Remote logger clear failed: {exc}")
            log_update(
                f"Remote logger ready: object_view_id={ORCHESTRATOR_LOGGER_ID}, "
                f"endpoint={os.environ.get('LETTA_LOGGER_API', 'http://100.80.49.10:8284/libraries/local-php-api')}"
            )

        from a2a_communicating_agents.agent_messaging.memory_backend import MemoryBackend

        if DocumentManager is None:
            print(f"[{AGENT_NAME}] DocumentManager unavailable (missing chromadb); using null memory backend.")

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

        class _NullMemoryFactory:
            @staticmethod
            async def create_memory_async(agent_id=None, **_kw):
                return ("null", _NullMemory())

        self.dispatcher = A2ADispatcher(
            workspace_root=WORKSPACE_ROOT,
            memory_factory=_NullMemoryFactory,
            document_manager_cls=DocumentManager,
        )
        self.model_id = model_id or os.getenv("ORCHESTRATOR_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5.3-codex"
        self.codex_path = shutil.which("codex") or "/usr/local/bin/codex"

        # Resolve LLM client for the LLMRouter
        if llm_client is not None:
            resolved_client = llm_client
            log_update("Using injected LLM client.")
        elif os.path.exists(self.codex_path):
            resolved_client = None  # LLMRouter uses codex CLI when client is None
            log_update(f"Using codex CLI at {self.codex_path} with model {self.model_id}.")
        elif os.environ.get("OPENAI_API_KEY") and OpenAI is not None:
            resolved_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            log_update("Using OpenAI SDK with OPENAI_API_KEY.")
        else:
            resolved_client = None
            log_update("No LLM configured. FallbackRouter will handle all messages.")

        # Build the router chain (injectable for tests)
        self._router: IRouter = router or RouterChain([
            LLMRouter(client=resolved_client),
            FallbackRouter(),
        ])

        self.doc_manager = DocumentManager() if DocumentManager is not None else None
        self._processed_message_ids: Set[str] = set()
        self._message_order: Deque[str] = deque(maxlen=200)
        self._self_aliases = self._build_self_aliases()
        self._ignored_senders = {
            AGENT_NAME.lower(), "orchestrator",
            "coder-agent", "tester-agent", "dashboard-agent",
        }
        self._self_profile = self._load_self_profile()
        self.messenger = AgentMessenger(agent_id=AGENT_NAME)
        self._running = False
        self._last_message_monotonic = time.monotonic()
        self._last_idle_heartbeat_sec = 0.0
        log_update(
            f"Startup complete: model_id={self.model_id}, "
            f"router={type(self._router).__name__}, "
            f"self_profile_keys={list(self._self_profile.keys())}"
        )

    # ========================================================================
    # IDENTITY HELPERS
    # ========================================================================

    def _build_self_aliases(self) -> Set[str]:
        aliases = {
            AGENT_NAME,
            AGENT_NAME.replace("-", " "),
            AGENT_NAME.replace("_", " "),
            AGENT_NAME.replace("-", ""),
            AGENT_NAME.replace("_", ""),
            "orchestrator",
            "the orchestrator",
            "orchestrator agent",
            "orchestrator-agent",
        }
        normalized = {self._normalize_identifier(a) for a in aliases if a}
        return {a for a in normalized if a}

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    def _is_self_reference(self, value: Optional[str]) -> bool:
        if not value:
            return False
        if self._normalize_identifier(value) in self._self_aliases:
            return True
        return "orchestrator" in value.lower()

    def _resolve_known_agent(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        normalized = self._normalize_identifier(str(value))
        for agent_name in self.known_agents:
            if self._normalize_identifier(agent_name) == normalized:
                return agent_name
        return None

    def _load_self_profile(self) -> Dict[str, Any]:
        agent_card = WORKSPACE_ROOT / "agent.json"
        if not agent_card.exists():
            return {}
        try:
            with agent_card.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            log_update(f"Failed to read agent card: {exc}")
            return {}

    def _get_topic_for_agent(self, agent_name: str) -> str:
        topic_map = {
            "coder-agent": "code",
            "tester-agent": "test",
            "dashboard-agent": "ops",
        }
        return topic_map.get(agent_name, agent_name)

    def _format_memory_status(self, memory_info: Dict[str, Any]) -> str:
        backend = memory_info.get("backend") or "unknown"
        namespace = memory_info.get("namespace")
        connected = memory_info.get("connected")
        status = "connected" if connected else ("disconnected" if connected is False else "unknown")
        namespace_segment = f" ({namespace})" if namespace else ""
        return f"{backend}{namespace_segment} -> {status}"

    # ========================================================================
    # AGENT DISCOVERY
    # ========================================================================

    async def discover_agents(self):
        log_update("Scanning workspace for agents...")
        registry = await self.dispatcher.refresh_registry()
        snapshot = self.dispatcher.routing_snapshot()
        log_update(
            f"Registry refresh complete: {len(registry)} agent cards loaded, "
            f"{len(snapshot)} routable entries visible."
        )
        for agent_name, info in snapshot.items():
            if not agent_name or self._is_self_reference(agent_name):
                continue
            memory_info = info.get("memory") or {}
            self.known_agents[agent_name] = {
                "description": info.get("description"),
                "capabilities": info.get("capabilities", []),
                "topics": info.get("topics", []),
                "memory": memory_info,
            }
            log_update(
                f"Discovered agent={agent_name}, "
                f"memory={self._format_memory_status(memory_info)}"
            )
        log_update(
            f"Discovery finished: {len(self.known_agents)} agents -> {list(self.known_agents.keys())}"
        )
        if "letta" not in self.known_agents:
            log_update(
                "[DISCOVERY WARNING] 'letta' agent card not found; direct questions about Jeri/Le(t)ta cannot be answered with agent existence details."
            )
        else:
            log_update("[DISCOVERY] 'letta' agent is registered and routable.")

    # ========================================================================
    # MESSAGE HANDLER (OBSERVER PATTERN)
    # ========================================================================

    async def _handle_message(self, message: AgentMessage):
        self._last_message_monotonic = time.monotonic()
        message_id = self._extract_message_id(message)
        if self._message_seen(message_id):
            return

        sender_name = (message.from_agent or "").strip().lower()
        log_update(
            f"Incoming message: id={message_id}, from='{message.from_agent}', "
            f"ignored={sender_name in self._ignored_senders}, "
            f"preview='{(message.content or '')[:120]}'"
        )

        if self._is_self_reference(sender_name) or sender_name in self._ignored_senders:
            self._mark_message_processed(message_id)
            log_update(
                f"[IGNORE] Sub-agent/self message filtered: sender='{sender_name}', "
                f"id={message_id}. Dashboard-ui receives this via its own subscription."
            )
            return

        try:
            content = message.content

            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()

            if content.strip().startswith("{") and "jsonrpc" in content:
                log_update(f"Skipping machine response: {content[:120]}...")
                self._mark_message_processed(message_id)
                return

            _content_lower = content.lower()
            _agent_response_patterns = [
                "**coder agent", "**tester agent", "**dashboard agent",
                "coder agent response", "tester agent response",
                "agent - code generated", "agent response",
            ]
            if any(pat in _content_lower for pat in _agent_response_patterns):
                log_update(f"Skipping agent response (content match): {content[:120]}...")
                self._mark_message_processed(message_id)
                return

            log_update(f"Processing: id={message_id}, preview='{content[:160]}'")

            context = RoutingContext(
                known_agents=dict(self.known_agents),
                orchestrator_name=AGENT_NAME,
                orchestrator_description=self._self_profile.get("description", ""),
                codex_path=self.codex_path,
                model_id=self.model_id,
                planner_root=PLANNER_ROOT,
            )

            decision: RouteDecision = await self._router.route(content, context)
            log_update(
                f"[ROUTE] target='{decision.target}', reasoning='{decision.reasoning}', response_preview='{decision.response[:120]}'"
            )

            if self._should_delegate_report_request(content):
                report_agent = self._resolve_report_agent()
                if report_agent and not self._is_self_reference(report_agent):
                    if decision.target == SELF or self._is_self_reference(decision.target):
                        decision = RouteDecision(
                            target=report_agent,
                            response=decision.response,
                            reasoning="Matched report-writing request and selected report-capable specialist.",
                        )
                        log_update(
                            f"[REPORT ROUTE] Overriding router decision to delegate to '{report_agent}'."
                        )
                else:
                    log_update("[REPORT ROUTE] No report-capable agent registered; keeping router decision.")

            if decision.target == SELF or self._is_self_reference(decision.target):
                reply = decision.response.strip()
                if not reply:
                    reply = self._direct_agent_existence_reply(content)
                    log_update(f"[DIRECT ANSWER FALLBACK] Generated response: {reply[:160]}")
                await self._send_direct_reply(reply)
                self._mark_message_processed(message_id)
                return

            target_agent = self._resolve_known_agent(decision.target)
            if not target_agent:
                log_update(f"Unknown agent '{decision.target}' suggested; falling back to direct reply.")
                await self._send_direct_reply(
                    f"I wanted to route this to '{decision.target}' but that agent isn't registered. "
                    "Could you rephrase or let me know which agent you'd like?"
                )
                self._mark_message_processed(message_id)
                return

            await self._delegate_to_agent(target_agent, content, decision)
            self._mark_message_processed(message_id)

        except Exception as exc:
            log_update(f"Error processing message: {exc}")
            import traceback
            traceback.print_exc()
            self._mark_message_processed(message_id)

    def _should_delegate_report_request(self, request: str) -> bool:
        request_lower = request.lower()
        if "report" not in request_lower:
            return False
        if "status" in request_lower or "health" in request_lower:
            return False
        return any(keyword in request_lower for keyword in _REPORT_KEYWORDS)

    def _resolve_report_agent(self) -> Optional[str]:
        preferred_names = (
            "report-agent",
            "report-writer",
            "report-writer-agent",
            "writing-agent",
            "writer-agent",
            "jerry",
            "jeri",
            "letta",
        )
        normalized_preferred = {self._normalize_identifier(name) for name in preferred_names}
        for agent_name in self.known_agents:
            if self._normalize_identifier(agent_name) in normalized_preferred:
                return agent_name
        return None

    def _direct_agent_existence_reply(self, request: str) -> str:
        request_lower = request.lower()
        if "jeri" in request_lower or "jerri" in request_lower:
            if self._resolve_report_agent() or "letta" in self.known_agents:
                return (
                    "Yes — the Jeri/Jerri request maps to a report-capable specialist in this workspace. "
                    "I can route report-writing requests there."
                )
            return (
                "I do not currently see a Jeri/Jerri or report-capable specialist registered in this workspace."
            )
        return "I'm here and ready to help."

    async def _send_direct_reply(self, response: str) -> None:
        log_update(f"Sending direct reply: preview='{response[:160]}'")
        await self.messenger.send_to_agent(
            agent_id="board",
            message=response,
            topic="orchestrator",
        )
        log_update("PASS: direct reply sent — ready for next request finished")

    async def _delegate_to_agent(
        self, target_agent: str, content: str, decision: RouteDecision
    ) -> None:
        log_update(
            f"[ROUTE] Delegating to '{target_agent}': {decision.reasoning}"
        )
        delegation = await self.dispatcher.delegate(
            agent_name=target_agent,
            description=content,
            context={},
            artifacts=None,
        )
        rpc_payload = json.dumps(delegation["payload"])
        target_topic = delegation["topic"]

        log_update(
            f"[SEND] topic='{target_topic}', agent={target_agent}, "
            f"payload_chars={len(rpc_payload)}"
        )
        dispatch_ok = await self.messenger.send_to_agent(
            agent_id="board",
            message=rpc_payload,
            topic=target_topic,
        )
        if dispatch_ok:
            log_update(f"[SEND SUCCESS] Delivered to topic='{target_topic}'")
        else:
            log_update(
                f"[SEND FAILURE] Not delivered to topic='{target_topic}'. "
                f"Agent={target_agent} will not receive the task."
            )

        confirmation = f"I've routed your request to **{target_agent}**.\nReasoning: {decision.reasoning}"
        await self.messenger.send_to_agent(
            agent_id="board",
            message=confirmation,
            topic="orchestrator",
        )
        log_update("PASS: message handled — ready for next request finished")

    # ========================================================================
    # ASYNC MAIN LOOP (OBSERVER PATTERN)
    # ========================================================================

    async def run_async(self):
        log_update("Started. Listening on topic 'orchestrator'...")
        self._running = True

        while self._running:
            try:
                log_update("Subscribing to orchestrator topic...")
                await self.messenger.subscribe("orchestrator", self._handle_message)
                log_update("Subscribed to 'orchestrator' topic. Waiting for messages...")
                self._log_transport_snapshot("Post-subscribe")
                log_update("PASS: orchestrator ready — subscribed and waiting for messages finished")
                try:
                    self.logger.clear_logs("ready.")
                except Exception:
                    pass

                asyncio.create_task(self.discover_agents())

                while self._running:
                    transport = getattr(self.messenger, "transport", None)
                    if transport is not None and not transport.is_connected():
                        log_update("WebSocket disconnected — reconnecting in 5s...")
                        break
                    now = time.monotonic()
                    idle_for = now - self._last_message_monotonic
                    if idle_for >= 30 and (now - self._last_idle_heartbeat_sec) >= 30:
                        self._last_idle_heartbeat_sec = now
                        self._log_transport_snapshot(
                            f"Idle heartbeat: {int(idle_for)}s since last message"
                        )
                    await asyncio.sleep(1)

            except KeyboardInterrupt:
                log_update("Shutting down...")
                self._running = False
                break
            except Exception as exc:
                log_update(f"Error in run loop: {exc}")
                import traceback
                traceback.print_exc()

            if not self._running:
                break

            log_update("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
            try:
                from a2a_communicating_agents.agent_messaging.transport_manager import TransportManager
                if TransportManager._transport:
                    try:
                        await TransportManager._transport.disconnect()
                    except Exception:
                        pass
                TransportManager._transport = None
                TransportManager._transport_name = None
                TransportManager._agent_id = None
                TransportManager._lock = None
                self.messenger._transport_initialized = False
                log_update("Transport reset — reconnecting now.")
            except Exception as reset_err:
                log_update(f"Transport reset warning: {reset_err}")

    def stop(self):
        self._running = False

    # ========================================================================
    # UTILITIES
    # ========================================================================

    def _extract_message_id(self, message) -> str:
        identifier = getattr(message, "document_id", None) or getattr(message, "id", None)
        if identifier:
            return str(identifier)
        timestamp = getattr(message, "timestamp", None)
        if timestamp is not None and hasattr(timestamp, "isoformat"):
            timestamp_value = timestamp.isoformat()
        else:
            timestamp_value = str(timestamp or time.time())
        content_hash = hash(getattr(message, "content", ""))
        return f"{timestamp_value}:{content_hash}"

    def _message_seen(self, message_id: str) -> bool:
        return bool(message_id and message_id in self._processed_message_ids)

    def _mark_message_processed(self, message_id: str) -> None:
        if not message_id:
            return
        if message_id in self._processed_message_ids:
            return
        if self._message_order.maxlen and len(self._message_order) >= self._message_order.maxlen:
            oldest = self._message_order.popleft()
            self._processed_message_ids.discard(oldest)
        self._message_order.append(message_id)
        self._processed_message_ids.add(message_id)

    def _log_transport_snapshot(self, prefix: str) -> None:
        transport = getattr(self.messenger, "transport", None)
        transport_name = getattr(self.messenger, "transport_name", None)
        if transport is None:
            log_update(f"{prefix}: transport=uninitialized")
            return
        connected = transport.is_connected() if hasattr(transport, "is_connected") else "unknown"
        queue_obj = getattr(transport, "_message_queue", None)
        queue_size = queue_obj.qsize() if queue_obj else "n/a"
        receiver_task = getattr(transport, "_receiver_task", None)
        receiver_running = bool(receiver_task and not receiver_task.done())
        ws_url = getattr(getattr(transport, "config", None), "url", "n/a")
        log_update(
            f"{prefix}: transport={transport_name}, connected={connected}, "
            f"receiver_running={receiver_running}, ack_queue={queue_size}, ws_url={ws_url}"
        )


def main():
    orchestrator = Orchestrator()
    try:
        asyncio.run(orchestrator.run_async())
    except KeyboardInterrupt:
        log_update("Shutting down gracefully...")


if __name__ == "__main__":
    main()
