"""The resources write path's narrow naming, for the apply wiring's providers.

The same move ``identity_port.py`` records, for the same reason: the apply
wiring's lazy providers are keyed by type, and the one type that would name
``ResourceFileService`` is a module that reaches the device dispatcher graph
at import time — importing it from the apply service (or eagerly from the DI
module) would turn a lazy provider into an import cycle. So this module
imports nothing, names the three methods the ``resources`` materialiser
calls, and serves as the provider key.

The bound object is the real ``ResourceFileService`` singleton — structural
typing, no adapter, no second implementation. A signature drift on the real
service surfaces as a ``TypeError`` in the materialiser's tests (whose fake
mirrors these shapes) before it surfaces mid-apply.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ManifestResourcePort(Protocol):
    """The three methods the ``resources`` materialiser reaches, as a type key.

    Signatures mirror the real service's keyword-only contract, so a drift
    there is caught by the materialiser's fake-driven tests first.
    """

    @abstractmethod
    async def upload_file(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        target_dir: str,
        filename: str,
        data: bytes,
        preserve_structure: bool = False,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def delete(
        self,
        *,
        entity_type: str = "staff",
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool: ...

    @abstractmethod
    async def exists(
        self,
        *,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        path: str,
    ) -> bool: ...


__all__ = ["ManifestResourcePort"]
