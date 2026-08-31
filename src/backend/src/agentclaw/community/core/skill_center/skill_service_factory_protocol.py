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
