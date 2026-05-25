"""
RouterChain — Chain of Responsibility pattern.

Single Responsibility: try each router in order and return the first
non-None decision. Knows nothing about routing logic itself.
"""
from __future__ import annotations

from typing import List, Optional

from .interfaces import IRouter, RouteDecision, RoutingContext


class RouterChain(IRouter):
    """Delegates to the first router in the list that commits to a decision."""

    def __init__(self, routers: List[IRouter]):
        if not routers:
            raise ValueError("RouterChain requires at least one router.")
        self._routers = routers

    async def route(self, request: str, context: RoutingContext) -> Optional[RouteDecision]:
        for router in self._routers:
            decision = await router.route(request, context)
            if decision is not None:
                return decision
        return None
