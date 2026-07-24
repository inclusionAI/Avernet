"""Public ``/openapi/v1/bots`` API surface (path move over existing handlers).

The gateway forwards ``/openapi/v1/bots/...`` here verbatim. This package does
**not** implement endpoints — it re-mounts the existing group routers under the
public prefix so the same handlers are reachable there, keeping the public
surface in a dedicated place, distinct from the legacy ``/api/...`` routers.
"""

from __future__ import annotations

from fastapi import APIRouter

from agentclaw.community.adapters.http.bot_management import (
    router as bot_management_module,
)
from agentclaw.community.adapters.http.channel.router import router as channel_router
from agentclaw.community.adapters.http.cron import router as cron_router
from agentclaw.community.adapters.http.identity.router import router as identity_router
from agentclaw.community.adapters.http.mcp import router as mcp_router
from agentclaw.community.adapters.http.resources import router as resources_router
from agentclaw.community.adapters.http.skill_center import skills, skillsets

from ._rehome import rehome_into

# (source router, prefix to strip). The agent-CRUD group (`/api/bots`) collapses
# onto the domain root so paths read `/openapi/v1/bots` and `/openapi/v1/bots/{id}`
# rather than `/openapi/v1/bots/bots`. Every other group keeps its name as a
# sub-path (`/openapi/v1/bots/channels`, `/openapi/v1/bots/mcp`, …).
_GROUPS = [
    (bot_management_module.router, "/api/bots"),
    (channel_router, "/api"),
    (identity_router, "/api"),
    (mcp_router, "/api"),
    (resources_router, "/api"),
    (cron_router, "/api"),
    (skills.router, "/api"),
    (skillsets.router, "/api"),
]


def build_public_router() -> APIRouter:
    """Assemble the re-homed ``/openapi/v1/bots`` router."""
    public = APIRouter(tags=["openapi-v1"])
    for source, strip_prefix in _GROUPS:
        rehome_into(public, source, strip_prefix=strip_prefix)
    return public


__all__ = ["build_public_router"]
