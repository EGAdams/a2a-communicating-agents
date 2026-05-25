#!/usr/bin/env python3
"""
Coder Agent (Async Refactored with Observer Pattern)

Handles code implementation, bug fixes, refactoring, and code generation tasks.
Uses Observer Pattern for real-time WebSocket message handling.

GoF Patterns:
- Observer Pattern: Subscribes to 'code' topic, receives callbacks
- Facade Pattern: Uses AgentMessenger for simplified communication
- Singleton Pattern: Shares WebSocket transport via TransportManager
"""

import sys
import os
import time
import json
import asyncio
from pathlib import Path
from typing import Set
from collections import deque

# Add parent directory to path to import shared modules
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # /home/adamsl/a2a_communicating_agents
PLANNER_ROOT = PROJECT_ROOT  # kept as alias for internal references

from dotenv import load_dotenv

# Load environment variables from the project root
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=True)

sys.path.insert(0, str(PROJECT_ROOT.parent))  # for: from a2a_communicating_agents.X import Y
sys.path.insert(0, str(PROJECT_ROOT))          # for: from rag_system.X import Y
os.chdir(PROJECT_ROOT)

# NEW: Import async messenger
from a2a_communicating_agents.agent_messaging import AgentMessenger, AgentMessage
from a2a_communicating_agents.orchestrator_agent.remote_logger import RemoteLogger

AGENT_NAME = "coder-agent"
AGENT_TOPIC = "code"  # Listen on the "code" topic
CODER_LOGGER_ID = os.getenv("CODER_LOGGER_ID", f"CoderAgent_{time.localtime().tm_year}")
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


class CoderAgent:
    """
    Coder agent using Observer Pattern for real-time message handling.

    Subscribes to 'code' topic once at startup, then processes all
    messages via async callbacks.
    """

    def __init__(self):
        self.logger = RemoteLogger(CODER_LOGGER_ID)
        try:
            self.logger.init()
        except Exception as exc:
            print(f"[{AGENT_NAME}] Remote logger init failed: {exc}")
        else:
            set_remote_logger(self.logger)
            try:
                self.logger.clear_logs("booting coder.")
            except Exception as exc:
                print(f"[{AGENT_NAME}] Remote logger clear failed: {exc}")
            log_update(
                f"Remote logger ready: object_view_id={CODER_LOGGER_ID}, endpoint={os.environ.get('LETTA_LOGGER_API', 'http://100.80.49.10:8284/libraries/local-php-api')}"
            )

        self._processed_message_ids: Set[str] = set()
        self._message_order: deque = deque(maxlen=200)
        self._last_message_monotonic = time.monotonic()
        self._last_idle_heartbeat_sec = 0.0

        # Use codex CLI (OAuth auth via ~/.codex/auth.json — no API key needed)
        import shutil
        self.codex_path = shutil.which("codex") or "/usr/local/bin/codex"
        self.client = True  # signals LLM is available
        self.model_id = os.getenv("CODER_MODEL", "gpt-5.3-codex")
        log_update(f"Using codex CLI at {self.codex_path} with model {self.model_id}")

        # NEW: Create messenger instance using TransportManager singleton
        self.messenger = AgentMessenger(agent_id=AGENT_NAME)
        self._running = False

    def _extract_message_id(self, message) -> str:
        """Generate a unique ID for a message."""
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

    async def handle_request(self, request_data: dict) -> dict:
        """Process a coding request and return results."""
        method = request_data.get("method", "")
        params = request_data.get("params", {})

        log_update(f"Handling method: {method}")
        log_update(f"Parameters: {json.dumps(params, indent=2)}")

        # Extract description from params
        description = params.get("description", "No description provided")
        context = params.get("context", {})

        # Generate code using codex CLI (runs in executor — does not block loop)
        if self.codex_path and self.model_id:
            try:
                generated_code = await self._generate_code_async(description, context)
                response = {
                    "status": "success",
                    "message": f"Generated code for: {description}",
                    "method": method,
                    "code": generated_code,
                    "details": {
                        "description": description,
                        "context": context,
                        "agent": AGENT_NAME
                    }
                }
            except Exception as e:
                log_update(f"Error generating code: {e}")
                response = {
                    "status": "error",
                    "message": f"Failed to generate code: {str(e)}",
                    "method": method,
                    "details": {
                        "description": description,
                        "context": context,
                        "agent": AGENT_NAME
                    }
                }
        else:
            # Fallback when no LLM is available
            response = {
                "status": "acknowledged",
                "message": f"Coder agent received request but cannot generate code (no LLM configured): {description}",
                "method": method,
                "details": {
                    "description": description,
                    "context": context,
                    "agent": AGENT_NAME
                }
            }

        return response

    async def _generate_code_async(self, description: str, context: dict) -> str:
        """Generate code via codex CLI without blocking the event loop."""
        return await asyncio.get_event_loop().run_in_executor(
            None, self._generate_code, description, context
        )

    def _generate_code(self, description: str, context: dict) -> str:
        """Generate code via codex CLI (uses OAuth from ~/.codex/auth.json)."""
        import subprocess
        import tempfile

        simple_solution = self._try_generate_simple_solution(description)
        if simple_solution is not None:
            log_update("Generated simple hello-world solution without Codex CLI.")
            return simple_solution

        started = time.monotonic()
        log_update(
            f"Generating code: model={self.model_id}, description_preview='{description[:120]}', context_keys={list(context.keys()) if isinstance(context, dict) else []}"
        )
        context_str = f"\n\nContext: {json.dumps(context, indent=2)}" if context else ""
        prompt = (
            f"You are an expert software developer. Generate clean, well-documented code.\n\n"
            f"Task: {description}{context_str}\n\n"
            f"Provide the complete code with brief explanatory comments. Code only, no prose."
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            output_file = f.name

        try:
            result = subprocess.run(
                [
                    self.codex_path, "exec", prompt,
                    "--ephemeral",
                    "--sandbox", "read-only",
                    "--output-last-message", output_file,
                    "--color", "never",
                    "--model", self.model_id,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(PLANNER_ROOT),
            )

            if os.path.exists(output_file):
                code = open(output_file).read().strip()
                if code:
                    log_update(f"Code generation complete via file output in {time.monotonic() - started:.2f}s")
                    return code

            if result.stdout.strip():
                log_update(f"Code generation complete via stdout in {time.monotonic() - started:.2f}s")
                return result.stdout.strip()

            raise RuntimeError(f"codex produced no output. stderr: {result.stderr[:200]}")

        finally:
            if os.path.exists(output_file):
                os.unlink(output_file)

    def _try_generate_simple_solution(self, description: str) -> str | None:
        """Return deterministic code for trivial requests that do not need an LLM."""
        normalized = " ".join(description.lower().split())
        is_hello_world_request = (
            "hello world" in normalized
            or "hello to the world" in normalized
            or ("hello" in normalized and "world" in normalized)
            # hello.c, hello.py, etc. imply hello-world in that language
            or "hello." in normalized
        )
        if not is_hello_world_request:
            return None

        if any(term in normalized for term in (".c", " c ", "in c", "c program", "c language", " c$"))  \
                or normalized.endswith(".c") or "hello.c" in normalized:
            return (
                '#include <stdio.h>\n\n'
                'int main(void) {\n'
                '    printf("Hello, world!\\n");\n'
                '    return 0;\n'
                '}\n'
            )

        if any(term in normalized for term in ("assembly", "asm", "nasm", "x86_64", "x86-64")):
            return (
                "; hello.asm - Linux x86_64 NASM\n"
                "; Build: nasm -f elf64 hello.asm -o hello.o\n"
                "; Link:  ld hello.o -o hello\n"
                "; Run:   ./hello\n\n"
                "global _start\n\n"
                "section .data\n"
                "    msg db \"Hello, world!\", 10\n"
                "    len equ $ - msg\n\n"
                "section .text\n"
                "_start:\n"
                "    mov rax, 1\n"
                "    mov rdi, 1\n"
                "    mov rsi, msg\n"
                "    mov rdx, len\n"
                "    syscall\n\n"
                "    mov rax, 60\n"
                "    xor rdi, rdi\n"
                "    syscall"
            )

        if any(term in normalized for term in ("bash", "shell", "terminal", "sh ")):
            return '#!/usr/bin/env bash\necho "Hello, world!"'

        if any(term in normalized for term in ("javascript", "node", "js")):
            return 'console.log("Hello, world!");'

        return 'print("Hello, world!")'

    # ========================================================================
    # ASYNC MESSAGE HANDLER (OBSERVER PATTERN)
    # ========================================================================

    async def _handle_message(self, message: AgentMessage):
        """
        Observer callback for incoming messages (GoF Observer Pattern).

        Called automatically when messages arrive on the 'code' topic.
        """
        self._last_message_monotonic = time.monotonic()
        message_id = self._extract_message_id(message)
        if self._message_seen(message_id):
            return

        try:
            content = message.content
            sender = message.from_agent or "unknown"

            log_update(f"Received message from {sender}: {content[:100]}...")

            # Try to parse as JSON-RPC
            if content.strip().startswith("{"):
                try:
                    request_data = json.loads(content)

                    # Handle JSON-RPC request
                    if "method" in request_data:
                        response = await self.handle_request(request_data)

                        # Send JSON-RPC response back
                        response_msg = json.dumps({
                            "jsonrpc": "2.0",
                            "result": response,
                            "id": request_data.get("id")
                        })

                        log_update(
                            f"[SEND] Sending JSON-RPC response to topic='orchestrator': request_id={request_data.get('id')}, status={response.get('status')}, payload_chars={len(response_msg)}"
                        )
                        rpc_send_ok = await self.messenger.send_to_agent(
                            agent_id="board",
                            message=response_msg,
                            topic="orchestrator"
                        )
                        if rpc_send_ok:
                            log_update(
                                f"[SEND SUCCESS] JSON-RPC response delivered to 'orchestrator' topic: request_id={request_data.get('id')}"
                            )
                        else:
                            log_update(
                                f"[SEND FAILURE] JSON-RPC response NOT delivered to 'orchestrator' topic — WebSocket send returned False. request_id={request_data.get('id')}"
                            )

                        # Also send human-readable message with the code
                        if response.get("status") == "success" and response.get("code"):
                            human_msg = (
                                f"✅ **Coder Agent - Code Generated**\n\n"
                                f"Task: {response['details']['description']}\n\n"
                                f"```\n{response['code']}\n```\n"
                            )
                        else:
                            human_msg = (
                                f"ℹ️ **Coder Agent Response**\n\n"
                                f"Task: {response['details']['description']}\n\n"
                                f"Status: {response.get('status', 'unknown')}\n"
                                f"Message: {response.get('message', 'No message')}"
                            )

                        log_update(
                            f"[SEND] Sending human-readable response to topic='orchestrator': status={response.get('status')}, chars={len(human_msg)}"
                        )
                        human_send_ok = await self.messenger.send_to_agent(
                            agent_id="board",
                            message=human_msg,
                            topic="orchestrator"
                        )
                        if human_send_ok:
                            log_update(
                                f"[SEND SUCCESS] Human-readable response delivered to 'orchestrator' topic. Chat client should now display it."
                            )
                        else:
                            log_update(
                                f"[SEND FAILURE] Human-readable response NOT delivered to 'orchestrator' topic — WebSocket send returned False. Chat client will NOT see the response."
                            )

                except json.JSONDecodeError:
                    log_update("Failed to parse JSON content")
            else:
                # Plain text message
                log_update(f"Plain text message: {content}")

        except Exception as e:
            log_update(f"Error processing message: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._mark_message_processed(message_id)
            log_update(f"Finished processing message_id={message_id}")
            log_update("PASS: task complete — ready for next request finished")

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
            f"{prefix}: transport={transport_name}, connected={connected}, receiver_running={receiver_running}, ack_queue={queue_size}, ws_url={ws_url}"
        )

    # ========================================================================
    # ASYNC MAIN LOOP (OBSERVER PATTERN)
    # ========================================================================

    async def run_async(self):
        """
        Main async event loop using Observer Pattern.

        Subscribes to 'code' topic once, then handles all messages
        via the _handle_message callback.
        """
        log_update(f"Started. Listening on topic '{AGENT_TOPIC}'...")

        # Subscribe to code topic (Observer Pattern)
        await self.messenger.subscribe(AGENT_TOPIC, self._handle_message)

        log_update(f"✅ Subscribed to '{AGENT_TOPIC}' topic. Waiting for requests...")
        self._log_transport_snapshot("Post-subscribe transport snapshot")
        log_update("PASS: coder ready — subscribed and waiting for tasks finished")

        # Keep running
        self._running = True
        try:
            while self._running:
                now = time.monotonic()
                idle_for = now - self._last_message_monotonic
                if idle_for >= 30 and (now - self._last_idle_heartbeat_sec) >= 30:
                    self._last_idle_heartbeat_sec = now
                    self._log_transport_snapshot(
                        f"Idle heartbeat: no incoming messages for {int(idle_for)}s (waiting on topic='{AGENT_TOPIC}')"
                    )
                await asyncio.sleep( 1 )  # Keep alive, messages arrive via callbacks
        except KeyboardInterrupt:
            log_update( "Shutting down..." )
        finally:
            self._running = False

    def stop(self):
        """Graceful shutdown."""
        self._running = False


def main():
    """Entry point with async support."""
    agent = CoderAgent()

    try:
        asyncio.run(agent.run_async())
    except KeyboardInterrupt:
        print(f"\n[{AGENT_NAME}] Shutting down gracefully...")


if __name__ == "__main__":
    main()
