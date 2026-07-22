"""Per-route auth requirement metadata (OpenAPI `x-avernet-security`).

Each public route declares the auth it requires via an OpenAPI extension the
gateway's route-security compiler reads. The format mirrors
``src/gateway/docs/2026-07-21-auth-design.md`` §8.1: a list of OR alternatives,
each a map of ``strategy -> params``.

v1 routes require an authenticated **user principal**
(``gateway.community.spi.authn.UserPrincipal``). Attach the requirement with::

    @router.get("/{bot_id}", openapi_extra=requires_user_principal())
    async def get_bot(bot_id: str) -> Envelope[Bot]: ...

This helper only emits the OpenAPI *requirement marker*; it is not the principal
itself (that domain model lives in ``spi/authn``). The scope vocabulary is
intentionally out of scope this session, so params are left empty.
"""

from typing import Any


def requires_user_principal() -> dict[str, Any]:
    """OpenAPI extra marking a route as requiring an authenticated user principal."""
    return {"x-avernet-security": [{"first_party_user": {}}]}
