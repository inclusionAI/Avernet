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
from ._requery import drop_parameter
from ._shim import legacy_router

_CONFIG = "/openapi/v1/bots/{bot_id}/engine/config"

#: The handler descriptions name the stage parameter; this address does not have
#: one, so they are reworded rather than republished as written.
_DROP_STAGE = drop_parameter("stage", {
    ("GET", _CONFIG): ("Reads the runtime named by the stage parameter — the bot's own workspace\nunless a published one is asked for.", "Reads the bot's own workspace. Reaching a published runtime is offered at\nthe address that replaces this one."),
    ("PUT", _CONFIG): ("Writes the bot's own workspace. A published runtime is what a release\nproduced and is replaced by publishing again, never edited, so naming one is\nrefused and nothing is written.", "Writes the bot's own workspace."),
})


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
    transform=_DROP_STAGE,
)

# A reword written for a route that no longer exists would be skipped in
# silence, republishing the description it was written to remove.
_DROP_STAGE.verify_all_applied()

__all__ = ["router"]
