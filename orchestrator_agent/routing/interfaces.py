"""
Routing interfaces — value objects and the IRouter contract.

Every router in the chain implements IRouter. RouteDecision and RoutingContext
are frozen value objects: they carry data, own no behaviour, and are never mutated.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# Sentinel: target value that means "orchestrator answers directly, do not delegate"
SELF = "__self__"


@dataclass(frozen=True)
class RouteDecision:
    """
    The outcome of a routing pass.

    When target == SELF the orchestrator sends `response` back to the user.
    When target is an agent name the orchestrator delegates and `response` is ignored.
    """
    target: str               # canonical agent name or SELF
    response: str             # direct reply text; meaningful only when target == SELF
    reasoning: str
    method: str = "agent.execute_task"


@dataclass(frozen=True)
class RoutingContext:
    """
    Read-only snapshot of everything a router needs to make a decision.

    Built fresh for every incoming message so routers always see the current
    set of registered agents without any shared mutable state.
    """
    known_agents: Dict[str, Any]        # name → agent card metadata
    orchestrator_name: str
    orchestrator_description: str
    codex_path: str
    model_id: str
    planner_root: Path


class IRouter(ABC):
    """
    Contract for every router in the chain.

    route() returns a RouteDecision when it can commit to a decision,
    or None to pass responsibility to the next router in the chain.
    """

    @abstractmethod
    async def route(self, request: str, context: RoutingContext) -> Optional[RouteDecision]:
        """Return a decision or None to defer to the next router."""
