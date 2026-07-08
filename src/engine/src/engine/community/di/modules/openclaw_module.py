"""OpenClaw composition root (DI module) — DEFERRED TO F5.

Intended end-state: the only core/api-side module that imports
`plugins/community/openclaw`, wiring the concrete `OpenClawPlugin` impl → the 12
`core/adapters/openclaw` adapters → the assembled `OpenClawEngine`, and providing
that engine to the injector so `EngineManager._activate_engine` resolves it.

**Status: empty skeleton, NOT used in F2.** The F2 cutover (Group E) deliberately
did NOT route engine construction through the injector: both SDD reviewers flagged
that modifying `_activate_engine` to resolve via the injector pulls the F5
`Injected()` migration forward and would pollute the ~1300 tests that drive
`EngineManager.get_instance()` via `reset_instance()`/patching. Instead, F2's
`OpenClawEngine.__init__` **self-assembles** from the ACL (the engine aggregate is
the F2 composition root, importing `plugins/community/openclaw` directly). This module
+ the idiomatic injector wiring (`@provider`s, `attach_injector`, request-scoped
`Injected()`) land in **F5**, at which point it is added to `di/container.py`.
"""
from __future__ import annotations

from injector import Binder, Module


class OpenClawModule(Module):
    """Per-engine DI module for OpenClaw (mirrors backend's per-area modules).

    Empty skeleton — F2 uses engine self-assembly, not the injector (see module
    docstring). F5 adds the `@provider`s for the port impl, the adapters, and the
    assembled `OpenClawEngine`, and registers this module in `di/container.py`.
    """

    def configure(self, binder: Binder) -> None:
        """No bindings — F2 self-assembles; the providers land in F5."""
