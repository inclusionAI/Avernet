"""Central DI container construction.

``build_injector`` is the single place the production module list lives. Each
per-area ``Module`` is added here as it lands (``ConfigModule`` in Task 20,
process/manager modules in Task 21, per-engine modules in F2+).

The base module list never branches on runtime mode. ``config`` carries the
mode-as-data; the caller (``api/app.py`` or a test fixture) reads it and decides
whether to layer ``Testing*`` override modules via ``extra_modules``. This
function does not branch on mode itself (Rule 14).
"""
from __future__ import annotations

from collections.abc import Iterable

from injector import Injector, Module

from engine.community.di.modules.config_module import ConfigModule
from engine.community.di.modules.manager_module import ManagerModule
from engine.community.di.modules.process_module import ProcessModule
from engine.community.di.modules.resource_materialization_module import (
    ResourceMaterializationModule,
)
from engine.community.di.modules.router_collection import SharedRoutersModule
from engine.community.di.profile_modules import modules_for
from engine.community.di.runtime_mode import RuntimeConfig


def build_injector(
    *,
    config: RuntimeConfig,
    extra_modules: Iterable[Module] | None = None,
) -> Injector:
    """Construct the application's ``Injector``.

    F1 builds the list incrementally as modules land (``ConfigModule`` in
    Task 20; process/manager modules in Task 21; per-engine modules in F2+).
    ``config`` carries mode-as-data so the signature is stable for callers and
    tests; this function does not branch on it.
    """
    modules: list[Module] = [
        ConfigModule(),
        ProcessModule(),
        ManagerModule(),
        ResourceMaterializationModule(),
        SharedRoutersModule(),
    ]
    modules.extend(modules_for(config.profile))
    if extra_modules:
        modules.extend(extra_modules)
    return Injector(modules)
