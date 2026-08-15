"""The legacy engine-config address.

``/openapi/v1/bots/{bot_id}/engine-config`` became
``/openapi/v1/bots/{bot_id}/engine/config``. The bot was a path segment before
and after, so these are the same handlers registered at the old address — the
one group in this package where nothing at all changed but the spelling.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.bots.engine_config import (
    router as engine_config_router,
)

from fastapi import APIRouter

from ._relocate import relocate
from ._shim import legacy_router


def _to_hyphenated(path: str) -> str | None:
    """``/{bot_id}/engine/config`` came from ``/{bot_id}/engine-config``."""
    head = "/openapi/v1/bots/{bot_id}/engine/config"
    if path != head:
        return None
    return "/openapi/v1/bots/{bot_id}/engine-config"


router: APIRouter = relocate(
    engine_config_router,
    legacy_router("/openapi/v1/bots", "bots"),
    _to_hyphenated,
)

__all__ = ["router"]
