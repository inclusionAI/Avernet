"""Shared authn parsing helpers used by the route table and the chain config."""

from __future__ import annotations

from gateway.community.spi.authn import PrincipalType


def parse_principal_type(name: str, *, source: str) -> PrincipalType:
    """Parse an identity-type string into a :class:`PrincipalType`.

    Raises a ``ValueError`` mentioning ``source`` (e.g. ``"route_security"``,
    ``"authn.yaml"``) so misconfiguration messages name where the bad value came
    from.
    """
    try:
        return PrincipalType(name)
    except ValueError as ex:
        raise ValueError(f"unknown identity type in {source}: {name!r}") from ex
