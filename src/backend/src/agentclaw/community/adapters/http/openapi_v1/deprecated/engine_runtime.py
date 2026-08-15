"""Legacy addresses for the groups whose bot was already a path parameter.

connection, engine, models, sessions, identity and the approvals *read* moved
from ``/openapi/v1/bots/<component>/{bot_id}/…`` to
``/openapi/v1/bots/{bot_id}/<component>/…``. Nothing else about them changed —
same handler, same parameters, same schemas — so the old address is the same
endpoint function registered a second time.

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

from ._relocate import bot_first_to_component_first, relocate
from ._shim import legacy_router

connection = relocate(
    connection_router,
    legacy_router("/openapi/v1/bots/connection", "connection"),
    bot_first_to_component_first("connection"),
)

engine = relocate(
    engine_router,
    legacy_router("/openapi/v1/bots/engine", "engine"),
    bot_first_to_component_first("engine"),
)

models = relocate(
    models_router,
    legacy_router("/openapi/v1/bots/models", "models"),
    bot_first_to_component_first("models"),
)

sessions = relocate(
    sessions_router,
    legacy_router("/openapi/v1/bots/sessions", "sessions"),
    bot_first_to_component_first("sessions"),
)

#: The approvals reads. The write shares this address and differs only by
#: method, so it is excluded here and shimmed in :mod:`.approvals`.
approvals = relocate(
    approvals_router,
    legacy_router("/openapi/v1/bots/approvals", "approvals"),
    bot_first_to_component_first("approvals"),
    skip=lambda method, _path: method == "PUT",
)

identity = relocate(
    identity_router,
    legacy_router("/openapi/v1/bots/identity", "identity"),
    bot_first_to_component_first("identity"),
)

#: Mounted with ``ENGINE_RUNTIME_ERROR_RESPONSES``, like their replacements.
ENGINE_RUNTIME = [connection, engine, models, sessions, approvals]

#: Mounted grant-checked, like its replacement.
GRANT_CHECKED = [identity]

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
