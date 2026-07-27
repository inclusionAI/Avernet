"""The authenticated identity set produced for one request (SPI contract).

The runner collects one ``Principal`` per required identity type into an
``Identities`` container; the delivery layer hands it to handlers and the
gateway forwards it downstream. Lived in ``spi`` (not ``core``) because adapters
consume it as a contract type at their boundary — the layer rules forbid
adapters from importing ``core``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ._models import Principal, PrincipalType


@dataclass(frozen=True)
class Identities:
    """The principals resolved for a request, keyed by identity type."""

    _principals: dict[PrincipalType, Principal]

    def get(self, principal_type: PrincipalType) -> Principal | None:
        """Return the principal of ``principal_type``, or ``None`` if absent."""
        return self._principals.get(principal_type)

    def require(self, principal_type: PrincipalType) -> Principal:
        """Return the principal of ``principal_type``; raise if absent."""
        p = self._principals.get(principal_type)
        if p is None:
            raise KeyError(f"no authenticated identity of type {principal_type}")
        return p

    def __iter__(self) -> Iterator[PrincipalType]:
        return iter(self._principals)

    def __len__(self) -> int:
        return len(self._principals)
