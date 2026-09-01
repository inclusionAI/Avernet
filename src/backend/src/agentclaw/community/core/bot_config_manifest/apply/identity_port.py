"""The identity write path's narrow naming, for the apply wiring's providers.

This is **not** a general service contract for ``IdentityService`` — that
service is deliberately protocol-less (one implementation; the identity
router records the waiver). It exists because the apply wiring's lazy
providers are keyed by type, and the one type that would name the identity
service is a module that reaches the device dispatcher graph at import time:
importing it from the apply service (or from the DI module, eagerly) would
turn a lazy provider into an import cycle.

So this module imports nothing, names the three methods the ``identity``
materialiser calls, and serves as the provider key. The bound object is the
real ``IdentityService`` singleton — structural typing, no adapter, no
second implementation — exactly the way ``SourceCredentialBinding``
satisfies W2's two seams by shape rather than by inheritance.

The method signatures are part of the contract Ripgrep can check against
``core/services/identity.py``; a drift there is caught by the materialiser's
tests, which drive fakes built on the same shapes the router calls.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

#: The router's fixed entity pair for the personal-bot surface — the pair
#: ``identity_coords_from_record`` resolves, supplied here so the port needs
#: no import of the module that carries it.
ENTITY_TYPE = "staff"


@runtime_checkable
class ManifestIdentityPort(Protocol):
    """The three methods the ``identity`` materialiser reaches, as a type key.

    Signatures mirror the real service's positional contract (entity_type,
    entity_id, bot_id, then the operation's own arguments, then owner or
    operator) so a signature drift on the real service surfaces as a
    ``TypeError`` in the materialiser's tests before it surfaces mid-apply.
    """

    async def list_bot_files(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> list[tuple[str, bool]]: ...

    async def read_identity_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> str: ...

    async def update_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        operator_id: str,
        engine_type: str | None = None,
        *,
        stage: str = "draft",
    ) -> Any: ...


__all__ = ["ENTITY_TYPE", "ManifestIdentityPort"]
