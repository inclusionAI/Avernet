"""Per-route auth requirement metadata (OpenAPI `x-avernet-security`).

Each public route declares the auth it requires via an OpenAPI extension the
gateway's route-security compiler reads. The format mirrors
``src/gateway/docs/2026-07-21-auth-design.md`` §8.1: a list of OR alternatives,
each a map of ``strategy -> params``.

v1 routes require an authenticated **user principal**. Attach it with::

    @router.get("/{bot_id}", openapi_extra=user_principal())
    async def get_bot(bot_id: str) -> Envelope[Bot]: ...

The scope vocabulary is intentionally out of scope this session, so params are
left empty.
"""

from typing import Any


def user_principal() -> dict[str, Any]:
    """OpenAPI extra marking a route as requiring an authenticated user."""
    return {"x-avernet-security": [{"first_party_user": {}}]}
