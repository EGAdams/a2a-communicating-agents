"""
LLMRouter — single LLM call per message.

The LLM either:
  - Responds with "DELEGATE: <agent-name>" to hand the task to a specialist, or
  - Writes a direct answer in plain text.

One call. One decision. No double-codex timeout.

The blocking subprocess / SDK call is wrapped in run_in_executor so the event
loop (and WebSocket keepalives) stay alive for the full duration of the call.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from contextlib import suppress
from typing import Any, Optional

from .interfaces import IRouter, RouteDecision, RoutingContext, SELF

_DELEGATE_PREFIX = "DELEGATE:"


def _build_prompt(request: str, context: RoutingContext) -> str:
    normalized_request = request.lower()
    if context.known_agents:
        agent_lines = "\n".join(
            f"  - {name}: {(meta.get('description') or 'specialist agent')}"
            for name, meta in context.known_agents.items()
        )
        agents_section = f"Available specialist agents:\n{agent_lines}"
    else:
        agents_section = "No specialist agents are currently registered."

    direct_answer_rules = (
        "Always answer direct factual questions about the orchestrator or available agents directly. "
        "If the user asks whether a specific agent exists or whether you can talk to that agent, answer with a clear yes/no and mention the agent name. "
        "Do not paraphrase such questions into vague status prompts."
    )

    if "jeri" in normalized_request:
        direct_answer_rules += (
            " The user mentioned Jeri/Jerri; treat this as a request to check whether the corresponding agent is registered and reachable."
        )

    return (
        f"You are the Orchestrator Agent managing a hub-and-spoke agent collective.\n\n"
        f"{agents_section}\n\n"
        f"Routing rules:\n"
        f"- {direct_answer_rules}\n"
        f"- Choose DELEGATE only when a specialist must handle an actual task.\n"
        f"- For existence, capability, or connectivity questions, respond directly.\n\n"
        f"User: {request}"
    )


def _parse_response(raw: str, request: str) -> RouteDecision:
    """Turn raw LLM text into a RouteDecision."""
    stripped = raw.strip()
    if stripped.upper().startswith(_DELEGATE_PREFIX):
        agent = stripped[len(_DELEGATE_PREFIX):].strip().strip('"').strip("'")
        return RouteDecision(
            target=agent,
            response="",
            reasoning="LLM delegation decision",
        )
    return RouteDecision(
        target=SELF,
        response=stripped or "I'm not sure how to respond to that.",
        reasoning="LLM direct response",
    )


class LLMRouter(IRouter):
    """
    Single Responsibility: make one LLM call and return a RouteDecision.

    Supports two LLM backends:
      - Injected OpenAI-SDK-compatible client (used in tests via dependency injection)
      - Codex CLI OAuth subprocess (production)
    """

    def __init__(self, client: Any = None):
        self._client = client

    async def route(self, request: str, context: RoutingContext) -> Optional[RouteDecision]:
        prompt = _build_prompt(request, context)
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                self._call_llm_sync,
                prompt,
                context,
            )
        except Exception as exc:
            # Log but don't crash — FallbackRouter is next in chain
            print(f"[LLMRouter] call failed: {exc}")
            return None

        if raw is None:
            return None
        return _parse_response(raw, request)

    # ------------------------------------------------------------------
    # Synchronous LLM backends (run in executor thread, not event loop)
    # ------------------------------------------------------------------

    def _call_llm_sync(self, prompt: str, context: RoutingContext) -> Optional[str]:
        if self._client is not None and hasattr(self._client, "chat"):
            return self._call_openai_sdk(prompt, context)
        return self._call_codex_cli(prompt, context)

    def _call_openai_sdk(self, prompt: str, context: RoutingContext) -> Optional[str]:
        try:
            completion = self._client.chat.completions.create(
                model=context.model_id,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = (completion.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                fence = "```json" if raw.startswith("```json") else "```"
                with suppress(IndexError):
                    raw = raw.split(fence, 1)[1].split("```", 1)[0].strip()
            return raw or None
        except Exception as exc:
            print(f"[LLMRouter] OpenAI SDK call failed: {exc}")
            return None

    def _call_codex_cli(self, prompt: str, context: RoutingContext) -> Optional[str]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            output_file = f.name
        try:
            result = subprocess.run(
                [
                    context.codex_path, "exec", prompt,
                    "--ephemeral",
                    "--sandbox", "read-only",
                    "--output-last-message", output_file,
                    "--color", "never",
                    "--model", context.model_id,
                ],
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(context.planner_root),
            )
            content = ""
            if os.path.exists(output_file):
                content = open(output_file).read().strip()
            if not content and result.stdout.strip():
                content = result.stdout.strip()
            return content or None
        except subprocess.TimeoutExpired:
            print("[LLMRouter] Codex CLI timed out")
            return None
        except Exception as exc:
            print(f"[LLMRouter] Codex CLI error: {exc}")
            return None
        finally:
            if os.path.exists(output_file):
                os.unlink(output_file)
