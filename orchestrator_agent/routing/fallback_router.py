"""
FallbackRouter — the last link in the chain. Never returns None.

Single Responsibility: guarantee that every message gets a response even when
the LLM is unavailable. Always routes to SELF with a safe default reply.
"""
from __future__ import annotations

from typing import Optional

from .interfaces import IRouter, RouteDecision, RoutingContext, SELF


class FallbackRouter(IRouter):
    """Always returns a RouteDecision targeting SELF. The chain never exhausts."""

    async def route(self, request: str, context: RoutingContext) -> Optional[RouteDecision]:
        description = context.orchestrator_description or "I coordinate tasks across the agent collective."
        return RouteDecision(
            target=SELF,
            response=(
                f"I'm the Orchestrator Agent. {description} "
                "I'm having trouble reaching my LLM right now — could you try again in a moment?"
            ),
            reasoning="All routers failed or returned no decision; using safe default.",
        )
