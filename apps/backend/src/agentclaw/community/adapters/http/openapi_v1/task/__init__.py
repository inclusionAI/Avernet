"""Task HTTP public adapters.

The router export stays lazy so Task-owned leaf modules (for example Todo error
mapping) can be imported by shared response plumbing without importing the router
back into that plumbing.
"""

from __future__ import annotations

from typing import Any

__all__ = ["task_router"]


def __getattr__(name: str) -> Any:
    if name != "task_router":
        raise AttributeError(name)

    from agentclaw.community.adapters.http.openapi_v1.task.router import router

    return router
