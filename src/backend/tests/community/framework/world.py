"""``World`` — handle on the per-test injector for seed + inspect.

A case author calls ``world.get(SomeService)`` from inside a ``seed``
callable or an ``extra_assertions`` callable to reach into the running
app's dependency graph. The same injector backs the FastAPI app under
test, so a row written via ``world.get(UserService).upsert_user(...)``
will be visible to the endpoint when the framework issues the request.

``World`` is intentionally thin: a typed ``.get()`` plus an
``.injector`` escape hatch. Richer affordances (e.g. ``world.db``,
``world.now``) can grow on demand.
"""
from __future__ import annotations

from typing import TypeVar

from injector import Injector


T = TypeVar("T")


class World:
    """Per-test handle on the live injector.

    Constructed by the ``world`` fixture; case authors receive a fully
    wired ``World`` instance.
    """

    def __init__(self, injector: Injector) -> None:
        self._injector = injector

    def get(self, cls: type[T]) -> T:
        """Resolve ``cls`` against the injector backing the app under test.

        The returned instance shares the same dependency graph the
        endpoint handler will resolve, so any state mutated through it
        is observable by the endpoint.
        """
        return self._injector.get(cls)

    @property
    def injector(self) -> Injector:
        """Escape hatch for the rare case ``world.get(...)`` isn't enough.

        Prefer ``.get()`` in case code; using ``.injector`` directly
        couples tests to the underlying DI library and makes future
        framework changes harder.
        """
        return self._injector
