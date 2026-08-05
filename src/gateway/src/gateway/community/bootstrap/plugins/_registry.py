"""Deprecated authn strategy registry helper.

Runtime authn strategy composition now lives in ``PluginContainer`` via the
``authn_strategies`` ``providers.Dict``. This small class is kept only as a
local, non-global test/compatibility collector; production bootstrap no longer
uses a module-level registry or import side-effect strategy registration.
"""

from __future__ import annotations

from collections.abc import Callable

from gateway.community.spi.authn import AuthStrategy


class AuthnStrategyRegistry:
    """Local per-name collector of authn strategies.

    Prefer DI providers for runtime code. This class has no process-global
    singleton and should not be used as a composition root.
    """

    def __init__(self) -> None:
        self._entries: dict[str, AuthStrategy | Callable[[], AuthStrategy]] = {}

    def register(
        self, name: str, strategy: AuthStrategy | Callable[[], AuthStrategy]
    ) -> None:
        self._entries[name] = strategy

    def resolve_all(self) -> dict[str, AuthStrategy]:
        result: dict[str, AuthStrategy] = {}
        for name, entry in self._entries.items():
            if callable(entry):
                result[name] = entry()
            else:
                result[name] = entry
        return result
