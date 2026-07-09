"""
EngineRegistry — name → engine class lookup.

EngineManager owns one of these and consults it during initialize() and
switch(). Engine implementations register themselves at import time (typically
from engines/<name>/__init__.py) by calling EngineRegistry.register(EngineClass).

Registration is idempotent: re-registering the same name with the same class
is a no-op; re-registering with a different class raises.
"""
from __future__ import annotations

from collections.abc import Iterable

from engine.community.core.engine.exceptions import EngineError, EngineNotFoundError
from engine.community.core.engine.naming import normalize
from engine.community.core.engine.protocol import Engine


class EngineRegistry:
    """In-memory mapping from engine name to engine class."""

    def __init__(self) -> None:
        self._engines: dict[str, type[Engine]] = {}

    def register(self, engine_class: type[Engine]) -> None:
        """Register an engine class under its `name` attribute.

        The class must expose a `name` class-level attribute or a property
        returning a stable identifier. The name is normalized (see
        :func:`engine.community.core.engine.naming.normalize`) before being used as the
        registry key, so legacy spellings (``"aicoding"`` /
        ``"aicoding"`` / ``"AiCoding"``) all land under the same canonical
        bucket. Conflicting registrations raise EngineError.
        """
        name = normalize(self._extract_name(engine_class))
        existing = self._engines.get(name)
        if existing is not None and existing is not engine_class:
            raise EngineError(
                f"Engine name {name!r} is already registered to "
                f"{existing.__name__}; refusing to overwrite with "
                f"{engine_class.__name__}"
            )
        self._engines[name] = engine_class

    def unregister(self, name: str) -> None:
        """Remove an engine registration. No-op if not present."""
        self._engines.pop(normalize(name), None)

    def get(self, name: str) -> type[Engine]:
        """Return the engine class for `name` or raise EngineNotFoundError.

        ``name`` is normalized before lookup so callers can pass any known
        alias (e.g. ``"aicoding"`` resolves to the ``aicoding`` registration).
        """
        canonical = normalize(name)
        try:
            return self._engines[canonical]
        except KeyError:
            raise EngineNotFoundError(canonical) from None

    def has(self, name: str) -> bool:
        return normalize(name) in self._engines

    def names(self) -> Iterable[str]:
        """Iterate registered engine names in registration order."""
        return tuple(self._engines.keys())

    def clear(self) -> None:
        """Remove all registrations (intended for tests)."""
        self._engines.clear()

    @staticmethod
    def _extract_name(engine_class: type[Engine]) -> str:
        """Pull the engine's name without instantiating it.

        Convention: engine classes should declare `name` as a class-level string
        attribute (e.g. `name = "openclaw"`). This branch is taken first and is
        the expected path.

        Fallback: if `name` is defined as a property, the class is instantiated
        with an empty config dict to read it. This only works if the engine's
        constructor tolerates `{}` as config and can compute `name` before
        initialize() is called. Authors who need non-trivial construction should
        prefer the class-attribute form.
        """
        name_attr = getattr(engine_class, "name", None)
        if isinstance(name_attr, str):
            return name_attr
        try:
            instance = engine_class({})
        except Exception as e:
            raise EngineError(
                f"Cannot determine name of {engine_class.__name__}: "
                f"class has no static `name` attribute and zero-arg "
                f"construction failed ({e!r}). "
                f"Declare `name` as a class-level string attribute."
            ) from e
        name = getattr(instance, "name", None)
        if not isinstance(name, str):
            raise EngineError(
                f"{engine_class.__name__}.name must return a string, got {name!r}"
            )
        return name


# Module-level singleton. Engine implementations register themselves at import
# time via `DEFAULT_REGISTRY.register(EngineClass)` from their package __init__.
# EngineManager consults this registry during initialize() and switch(). Tests
# that want a clean slate can call `DEFAULT_REGISTRY.clear()` — but then they
# must re-import the engine packages to repopulate it.
DEFAULT_REGISTRY = EngineRegistry()


__all__ = ["EngineRegistry", "DEFAULT_REGISTRY"]
