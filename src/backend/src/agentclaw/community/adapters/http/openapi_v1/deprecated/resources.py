"""Legacy resources addresses — the bot back in the query string.

Seven operations moved from ``/openapi/v1/bots/resources`` with a required
``bot_id`` query parameter to ``/openapi/v1/bots/{bot_id}/resources``. Nothing
else about them changed, so each legacy route is the current handler with one
parameter re-annotated, registered at the address it used to have — and its
response model and response table read off the real route rather than restated
(``download`` has its own, for the octet-stream body).

``require_granted_bot`` reads the bot off the path *or* the query string, so
these mount grant-checked exactly as their replacements do: the same code
deciding the same thing, which is what makes the parity claim true rather than
hopeful.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.resources import (
    router as resources_router,
)

from ._relocate import bot_first_to_query, relocate
from ._requery import LegacyBotIdQuery, deprecated_doc, with_query_parameter
from ._shim import legacy_router


def _bot_to_query(endpoint, method, new_path):
    return with_query_parameter(
        endpoint,
        "bot_id",
        LegacyBotIdQuery,
        doc=deprecated_doc(endpoint, f"{method} {new_path}"),
    )


router = relocate(
    resources_router,
    legacy_router("/openapi/v1/bots/resources", "resources"),
    bot_first_to_query("resources"),
    transform=_bot_to_query,
)

__all__ = ["router"]
