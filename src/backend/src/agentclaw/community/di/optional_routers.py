"""Binding key for routers whose mounting is runtime-mode dependent.

``api/app.py`` resolves :class:`OptionalRouters` from the injector and
mounts every contained ``APIRouter`` unconditionally. Prod boots see an
empty list (bound by :class:`InfrastructureModule`); local boots see a
populated list (bound by :class:`TestingInfrastructureModule`).

Lives in ``di/`` (not ``api/``) so the DI modules can import this
binding key without creating a ``di/ -> api/`` cycle. The actual router
modules under ``api/local/`` are imported lazily inside the local-mode
provider, so prod never loads them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter


@dataclass(frozen=True)
class OptionalRouters:
    """Injector-bound holder for runtime-mode-conditional routers."""

    routers: list[APIRouter] = field(default_factory=list)
