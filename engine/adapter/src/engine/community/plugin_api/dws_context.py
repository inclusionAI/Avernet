"""Read-only context enrichment; never starts an engine or sends a message."""

from typing import Protocol

from engine.community.kernel.dws_context import HostingContext
from engine.community.kernel.dws_hosting import Turn


class HostingContextProvider(Protocol):
    async def prepare(self, turn: Turn) -> HostingContext:
        """Resolve stable identities and available relations, not rule semantics.

        The snapshot is bound to the turn, rule version and recipient. Queries
        must use the authorized account. Empty search results are not negative
        relationship evidence. No message body can establish a trusted identity.
        allow means identity verified, never that a natural-language rule matches.
        Missing identity fails closed; missing relations stay unknown. Generation
        and review receive the complete rule and decide which facts it requires.
        """
        ...
