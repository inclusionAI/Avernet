"""Service API Protocol for the SkillService factory.

The factory mints :class:`SkillService` instances scoped to per-request
paths. Adapters call ``factory.create(...)`` to obtain a request-scoped
service.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillServiceFactoryProtocol(Protocol):
    """Service API for minting per-request SkillService instances."""

    def create(self, *args: Any, **kwargs: Any) -> Any: ...

    def local_skill_package_storage_for_locator(
        self,
        *,
        entity_id: str,
        owner_id: str,
        bot_id: str,
        engine_type: str | None,
        entity_type: str,
        is_desktop: bool,
        is_teclaw: bool,
        locator: str,
        skill_name: str,
    ) -> Any: ...
