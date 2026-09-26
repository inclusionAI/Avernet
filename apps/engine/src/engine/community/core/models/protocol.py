"""
ModelsService Protocol — model-catalogue plugin interface.

Each engine implementation under ``engines/<name>/models.py`` provides a class
that structurally satisfies this Protocol. ``EngineManager`` exposes the
active engine's models plugin via ``EngineManager.get_instance().models``
(``None`` if the engine does not declare any model capabilities).

Method names follow the domain-specific form (``list_models``,
``list_providers``) to match the cron / skills plugins and avoid clashing
with call sites that already speak in generic vocabulary.

Selection semantics
-------------------
``MODEL_LIST`` — :meth:`list_models` returns the catalogue.
``MODEL_SWITCH`` — there is no dedicated ``switch`` RPC; the caller sets
``chat.send`` ``model`` per-request, so "switch" is effectively stateless.
Declaring the capability tells the frontend the picker is functional.

See ``src/engine/docs/heterogeneous-engine-architecture.md`` for the
plugin-first engine architecture rationale.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext
from engine.community.core.models.models import Model, Provider


@runtime_checkable
class ModelsService(Protocol):
    """Backend talks to the model catalogue through this Protocol."""

    async def list_models(
        self, auth: AuthContext | None = None,
    ) -> list[Model]:
        """Return every model the engine can route to.

        The order is engine-defined; adapters should forward the relay's
        order verbatim so the frontend can rely on whatever grouping the
        backend chose.
        """
        ...

    async def list_providers(
        self, auth: AuthContext | None = None,
    ) -> list[Provider]:
        """Return the providers, each nesting its supported models.

        This is a convenience view for UIs that group by vendor; the flat
        :meth:`list_models` result is the canonical source.
        """
        ...


__all__ = ["ModelsService"]
