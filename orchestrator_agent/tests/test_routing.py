"""
Tests for the routing package.

Each router class is tested independently (SRP in tests too):
- LLMRouter with an injected mock client
- FallbackRouter as a safe-default guarantee
- RouterChain for correct chain-of-responsibility ordering
"""
import asyncio
import pytest
from unittest.mock import MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routing import (
    RouteDecision, RoutingContext, SELF,
    LLMRouter, FallbackRouter, RouterChain,
)
from pathlib import Path


def make_context(known_agents=None):
    return RoutingContext(
        known_agents=known_agents or {
            "coder-agent": {"description": "Writes code"},
            "tester-agent": {"description": "Runs tests"},
        },
        orchestrator_name="orchestrator-agent",
        orchestrator_description="Coordinates tasks.",
        codex_path="/usr/local/bin/codex",
        model_id="gpt-test",
        planner_root=Path("/tmp"),
    )


def make_sdk_client(response_text: str):
    """Build a minimal OpenAI-SDK-shaped mock."""
    message = MagicMock()
    message.content = response_text
    choice = MagicMock()
    choice.message = message
    completion = MagicMock()
    completion.choices = [choice]
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    return client


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# LLMRouter
# ---------------------------------------------------------------------------

def test_llm_router_delegates_when_llm_says_delegate():
    client = make_sdk_client("DELEGATE: coder-agent")
    router = LLMRouter(client=client)
    decision = run(router.route("write a hello world", make_context()))
    assert decision is not None
    assert decision.target == "coder-agent"


def test_llm_router_returns_direct_response_for_plain_text():
    client = make_sdk_client("I have access to coder-agent and tester-agent.")
    router = LLMRouter(client=client)
    decision = run(router.route("what agents do you have?", make_context()))
    assert decision is not None
    assert decision.target == SELF
    assert "coder-agent" in decision.response


def test_llm_router_answers_agent_existence_questions_directly():
    client = make_sdk_client("Yes — the letta agent exists and I can talk to it through the memory topic.")
    router = LLMRouter(client=client)
    decision = run(router.route("Does Jeri exist and can you talk to that agent?", make_context({"letta": {"description": "Memory specialist"}})))
    assert decision is not None
    assert decision.target == SELF
    assert "Yes" in decision.response


def test_llm_router_returns_none_on_client_failure():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("network error")
    router = LLMRouter(client=client)
    decision = run(router.route("hello", make_context()))
    assert decision is None


def test_llm_router_strips_delegate_prefix_case_insensitively():
    client = make_sdk_client("DELEGATE: tester-agent")
    router = LLMRouter(client=client)
    decision = run(router.route("run the test suite", make_context()))
    assert decision.target == "tester-agent"


# ---------------------------------------------------------------------------
# FallbackRouter
# ---------------------------------------------------------------------------

def test_fallback_router_never_returns_none():
    router = FallbackRouter()
    decision = run(router.route("anything at all", make_context()))
    assert decision is not None


def test_fallback_router_always_targets_self():
    router = FallbackRouter()
    decision = run(router.route("something", make_context()))
    assert decision.target == SELF


def test_fallback_router_response_is_non_empty():
    router = FallbackRouter()
    decision = run(router.route("ping", make_context()))
    assert decision.response.strip() != ""


def test_orchestrator_direct_existence_reply_mentions_report_capable_agent_when_registered():
    from orchestrator_agent.main import Orchestrator

    orchestrator = Orchestrator(router=RouterChain([FallbackRouter()]), llm_client=None, model_id="gpt-test")
    orchestrator.known_agents = {"report-writer-agent": {"description": "Writes reports"}}
    reply = orchestrator._direct_agent_existence_reply("Does Jeri exist and can you talk to that agent?")
    assert "Yes" in reply
    assert "report-capable" in reply.lower()
    assert orchestrator._resolve_report_agent() == "report-writer-agent"


def test_orchestrator_prefers_report_agent_for_report_requests():
    from orchestrator_agent.main import Orchestrator

    orchestrator = Orchestrator(router=RouterChain([FallbackRouter()]), llm_client=None, model_id="gpt-test")
    orchestrator.known_agents = {
        "report-writer-agent": {"description": "Writes reports"},
        "coder-agent": {"description": "Writes code"},
    }
    assert orchestrator._should_delegate_report_request("get Jerry to help us write reports")
    assert orchestrator._resolve_report_agent() == "report-writer-agent"


# ---------------------------------------------------------------------------
# RouterChain
# ---------------------------------------------------------------------------

def test_router_chain_uses_first_non_none_result():
    client = make_sdk_client("DELEGATE: coder-agent")
    chain = RouterChain([LLMRouter(client=client), FallbackRouter()])
    decision = run(chain.route("write some code", make_context()))
    assert decision.target == "coder-agent"


def test_router_chain_falls_through_to_fallback_when_llm_fails():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("down")
    chain = RouterChain([LLMRouter(client=client), FallbackRouter()])
    decision = run(chain.route("anything", make_context()))
    assert decision is not None
    assert decision.target == SELF


def test_orchestrator_routes_report_requests_to_specialist():
    from orchestrator_agent.main import Orchestrator

    orchestrator = Orchestrator(router=RouterChain([FallbackRouter()]), llm_client=None, model_id="gpt-test")
    orchestrator.known_agents = {
        "report-writer-agent": {"description": "Writes reports"},
        "coder-agent": {"description": "Writes code"},
    }
    assert orchestrator._should_delegate_report_request("get Jerry to help us write reports")
    assert orchestrator._resolve_report_agent() == "report-writer-agent"


def test_router_chain_rejects_empty_list():
    with pytest.raises(ValueError):
        RouterChain([])
