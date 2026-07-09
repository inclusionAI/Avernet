"""Single source of truth for the ``Testing*`` override-module list.

``build_injector`` provides the production base stack and does not branch on
runtime mode. The caller decides which testing overrides to layer via
``extra_modules``; keeping the mode → module mapping here keeps the call sites
(the ``test_injector`` fixture today; ``api/app.py``'s local boot later) from
drifting apart. Mirror of ``src/backend/src/agentclaw/di/testing_modules.py``.

F1: there are no mode-keyed engine testing modules yet — the only test override
is the per-test :class:`TestingConfigModule`, which the fixture appends
directly. ``plugins/local/<engine>/`` mock modules land in F6 and register here.
"""
from __future__ import annotations

from injector import Module

from engine.community.di.runtime_mode import RuntimeConfig


def testing_modules_for(config: RuntimeConfig) -> list[Module]:
    """Return the ``Testing*`` modules to layer for ``config``.

    Empty in F1 (no local mock plugins yet). Kept so call sites are stable and
    F6 can register ``plugins/local`` overrides keyed on ``config.runtime``.
    """
    modules: list[Module] = []
    return modules
