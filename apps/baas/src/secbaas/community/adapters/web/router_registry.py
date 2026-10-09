"""Router registry — allows enterprise to register additional FastAPI routers.

Community's create_app() calls get_extra_routers() to discover and mount
any routers registered by enterprise (or other extensions) at import time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

_router_factories: list[Callable[[], APIRouter]] = []


def register_router(factory: Callable[[], APIRouter]) -> None:
    """Register a router factory for discovery by create_app().

    Enterprise calls this at import time. The factory is deferred so
    that router module imports only happen when create_app() runs.
    """
    _router_factories.append(factory)


def get_extra_routers() -> list[APIRouter]:
    """Instantiate and return all registered routers."""
    return [f() for f in _router_factories]
