"""Legacy addresses for the groups whose bot was already a path parameter.

connection, engine, models, sessions, identity and the approvals *read* moved
from ``/openapi/v1/bots/<component>/{bot_id}/…`` to
``/openapi/v1/bots/{bot_id}/<component>/…``. For all but identity nothing else
changed — same handler, same parameters, same schemas — so the old address is
the same endpoint function registered a second time.

**Identity is the exception.** Its handlers later gained a ``stage`` parameter,
which the retiring address must not publish (see ``_REWORDS`` below), so it is
registered through a transform rather than as-is.

The approvals *write* is not here. Its body lost ``session_key``, so the old
address needs a body the new handler no longer accepts; it is shimmed by hand
in :mod:`.approvals`.

Two routers rather than one, because the two halves are mounted differently and
the mount is part of what a caller experiences. The engine-runtime groups
document a 501 and 504 and resolve their own owner; identity is grant-checked
at the mount. Merging them would give one half the other's contract.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.connection import (
    router as connection_router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine import (
    router as engine_router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.models import (
    router as models_router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import (
    router as sessions_router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.approvals import (
    router as approvals_router,
)
from agentclaw.community.adapters.http.openapi_v1.identity import (
    router as identity_router,
)

from fastapi import APIRouter

from ._relocate import bot_first_to_component_first, relocate
from ._requery import drop_parameter
from ._shim import legacy_router

#: Keep ``stage`` off identity's retiring addresses.
#:
#: The engine-runtime groups in this module publish ``stage`` at both addresses,
#: and should: they have taken it since before this package existed, so it is
#: part of the contract those addresses are frozen with. Identity gained it
#: after, which makes it a new capability — and a retiring address that grows
#: one is a reason not to migrate.
_IDENTITY = "/openapi/v1/bots/{bot_id}/identity"
_IDENTITY_FILE = f"{_IDENTITY}/{{file_type}}"

_DROP_STAGE = drop_parameter("stage", {
    ("GET", _IDENTITY): ("Every entry comes from the one runtime the stage parameter names, so a\nfile's presence is always reported for the runtime asked about.", "Every entry comes from the bot's own workspace."),
    ("GET", _IDENTITY_FILE): ("Reads the runtime named by the stage parameter — the bot's own workspace\nunless a published one is asked for.", "Reads the bot's own workspace. Reaching a published runtime is offered at\nthe address that replaces this one."),
    ("PUT", _IDENTITY_FILE): ("Writes the bot's own workspace. A published runtime is what a release\nproduced and is replaced by publishing again, never edited, so naming one is\nrefused and nothing is written.", "Writes the bot's own workspace."),
})

connection: APIRouter = relocate(
    connection_router,
    legacy_router("/openapi/v1/bots/connection", "connection"),
    bot_first_to_component_first("connection"),
)

engine: APIRouter = relocate(
    engine_router,
    legacy_router("/openapi/v1/bots/engine", "engine"),
    bot_first_to_component_first("engine"),
)

models: APIRouter = relocate(
    models_router,
    legacy_router("/openapi/v1/bots/models", "models"),
    bot_first_to_component_first("models"),
)

sessions: APIRouter = relocate(
    sessions_router,
    legacy_router("/openapi/v1/bots/sessions", "sessions"),
    bot_first_to_component_first("sessions"),
)

#: The approvals reads. The write shares this address and differs only by
#: method, so it is excluded here and shimmed in :mod:`.approvals`.
approvals: APIRouter = relocate(
    approvals_router,
    legacy_router("/openapi/v1/bots/approvals", "approvals"),
    bot_first_to_component_first("approvals"),
    skip=lambda method, _path: method == "PUT",
)

identity: APIRouter = relocate(
    identity_router,
    legacy_router("/openapi/v1/bots/identity", "identity"),
    bot_first_to_component_first("identity"),
    transform=_DROP_STAGE,
)

#: Mounted with ``ENGINE_RUNTIME_ERROR_RESPONSES``, like their replacements.
ENGINE_RUNTIME: list[APIRouter] = [connection, engine, models, sessions, approvals]

#: Mounted grant-checked, like its replacement.
GRANT_CHECKED: list[APIRouter] = [identity]

__all__ = [
    "ENGINE_RUNTIME",
    "GRANT_CHECKED",
    "approvals",
    "connection",
    "engine",
    "identity",
    "models",
    "sessions",
]

# A reword written for a route that no longer exists would be skipped in
# silence, republishing the description it was written to remove.
_DROP_STAGE.verify_all_applied()

