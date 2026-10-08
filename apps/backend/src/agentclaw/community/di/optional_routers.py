"""Binding key for routers whose mounting is runtime-mode dependent.

``adapters/http/boot.py``'s ``finalize_worker_runtime`` resolves
:class:`OptionalRouters` from the worker's injector and mounts every contained
``APIRouter`` unconditionally. Prod boots see an empty list (bound by
:class:`InfrastructureModule`); local boots see a populated list (bound by
:class:`TestingInfrastructureModule`).

This is the one route registration that happens in the composition root's
worker-runtime phase rather than at construction: it is the only place in the
tree that resolves a binding outside a request, so it has to wait for an
injector. Every other router is mounted at import, which is what lets a master
process preload the app and fork workers without building one.

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
