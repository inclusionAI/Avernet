"""``IdentityChain`` — the outer strategy that runs one identity's plugin chain.

Each identity type has one :class:`IdentityChain` holding the ordered
:class:`~gateway.community.spi.authn.AuthStrategy` plugins enabled for it. The
chain is itself an ``AuthStrategy`` (it has ``name``, ``principal_type``, and
``build``): it runs its inner strategies in order until one returns a
``Principal``:

- an inner strategy returning ``None`` (credential absent) → try the next;
- an inner strategy raising :class:`~gateway.community.spi.auth.AuthError`
  (credential present but invalid) → propagate immediately, do NOT fall back
  (design §5);
- an inner strategy returning a ``Principal`` → adopt it, after guarding that
  the principal's type matches the chain's ``principal_type``.

If every inner strategy is inapplicable, the chain returns ``None``. Deciding
whether ``None`` is acceptable (required vs optional) is the runner's job — the
chain only adjudicates "does this request carry a credential my identity
recognises".

Satisfies the :class:`~gateway.community.spi.authn.AuthStrategy` protocol
structurally (``name`` + ``principal_type`` + ``build``); the composition root
registers one ``IdentityChain`` per ``PrincipalType``.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AuthStrategy,
    CredentialBundle,
    Principal,
    PrincipalType,
)


class IdentityChain:
    """The ordered plugin chain for one identity type — itself an ``AuthStrategy``.

    Args:
        principal_type: the identity this chain resolves.
        strategies: the inner ``AuthStrategy``\\ s, in priority order.
    """

    def __init__(
        self,
        principal_type: PrincipalType,
        strategies: tuple[AuthStrategy, ...],
    ) -> None:
        self.principal_type = principal_type
        self._strategies = strategies

    @property
    def name(self) -> str:
        # A stable name for the chain (the identity's wire value); referenced by
        # config/observability. Inner strategy names stay on the inner plugins.
        return self.principal_type.value  # type: ignore[no-any-return]

    async def build(self, creds: CredentialBundle) -> Principal | None:
        """Try each inner strategy in order; return the first successful Principal."""
        for strategy in self._strategies:
            # AuthError (present-but-invalid) propagates and short-circuits the
            # chain; None (absent) falls through to the next strategy.
            principal = await strategy.build(creds)
            if principal is None:
                continue
            if principal.type is not self.principal_type:  # defensive: wrong type
                raise AuthError(
                    f"strategy {strategy.name!r} built wrong principal type for "
                    f"{self.principal_type.value!r}"
                )
            return principal
        return None  # every inner strategy was inapplicable
