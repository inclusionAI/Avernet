"""``dev_header`` strategy — a LOCAL-ONLY user identity from an explicit header.

The community ``user`` chain verifies real Google access tokens, which a dev
box has none of — so under the shipped fail-closed route table every request
through a locally run gateway 401s at the edge. This strategy exists for that
box alone: it maps the ``x-dev-user`` header's value onto a
:class:`UserPrincipal` verbatim, no verification, the same trust move
``BCS_AUTH_MOCK`` makes on the BCS side.

It is DOUBLE-gated, and both gates are environment variables rather than
config on purpose — config travels through overlays and repos, an env var is
set by the operator of the process:

- the bootstrap appends it to the ``user`` chain only when
  ``GATEWAY_AUTH_MOCK=1`` (``bootstrap/_authn.py``), so the shipped
  ``identity_strategies`` table never names it; and
- :meth:`build` itself answers ``None`` unless the same variable is set, so
  even a config overlay that declares ``dev_header`` in a chain gets an inert
  strategy, not an open door.

The env var is read per request, not cached at construction: the singleton is
built once per process, and reading late keeps a test's ``monkeypatch.setenv``
honest.
"""

from __future__ import annotations

import os

from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)

logger = get_logger("authn-dev-header")

#: The switch shared with ``bootstrap/_authn.py``; only the literal "1" enables.
AUTH_MOCK_ENV = "GATEWAY_AUTH_MOCK"

#: The header carrying the asserted user id. Requiring an explicit header keeps
#: every mock-authenticated request deliberate — an anonymous request still
#: fail-closes even with the mock enabled.
DEV_USER_HEADER = "x-dev-user"


def auth_mock_enabled() -> bool:
    """Whether the operator switched the dev auth mock on for this process."""
    return os.getenv(AUTH_MOCK_ENV, "").strip() == "1"


class DevHeaderUserStrategy:
    """Resolve the ``x-dev-user`` header into a ``UserPrincipal``, unverified."""

    name = "dev_header"
    principal_type = PrincipalType.USER

    def __init__(self, *, token_header: str = DEV_USER_HEADER) -> None:
        self._token_header = token_header

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if not auth_mock_enabled():
            return None  # gate 2: declared-but-not-enabled stays inert
        user_id = creds.headers.get(self._token_header, "").strip()
        if not user_id:
            return None  # header absent → not applicable; runner fail-closes
        logger.debug("dev auth header accepted: user=%s", user_id)
        subject = AuthenticatedUser(
            id=user_id,
            username=user_id,
            display_name=user_id,
        )
        # No tenant, same as the google strategy: an asserted user id says who
        # the person claims to be, never which tenant they act for.
        return UserPrincipal(subject=subject)
