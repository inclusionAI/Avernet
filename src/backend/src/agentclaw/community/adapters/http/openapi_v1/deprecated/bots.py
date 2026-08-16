"""The legacy engine-config address.

``/openapi/v1/bots/{bot_id}/engine-config`` became
``/openapi/v1/bots/{bot_id}/engine/config``. The bot was a path segment before
and after, so these are the same handlers registered at the old address.

One thing does change: the current address gained a ``stage`` query parameter,
and this one must not publish it. A published runtime is reachable only through
the address that is not going away — the retiring contract is frozen, and a new
capability here would be a reason to stay on it. ``without_parameter`` drops it
from the registered signature, so the handler's own default (the draft) applies
and the old address answers exactly as it always did.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.bots.engine_config import (
    router as engine_config_router,
)

from fastapi import APIRouter

from ._relocate import relocate
from ._requery import deprecated_doc, without_parameter
from ._shim import legacy_router


def _drop_stage(endpoint, method, new_path):
    return without_parameter(
        endpoint,
        "stage",
        doc=deprecated_doc(endpoint, f"{method} {new_path}"),
    )


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
    transform=_drop_stage,
)

__all__ = ["router"]
