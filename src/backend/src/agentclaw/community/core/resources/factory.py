"""Resource service factory.

Lives in ``core/`` so ``api/`` routes can reference the factory type
via ``Injected(ResourceServiceFactory)`` without importing the DI
wiring layer. Wired by :class:`ResourcesModule` via ``binder.bind``
(all its deps are pure-core bindings, so the injector can introspect
its ``@inject`` constructor directly).
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.resources.repository.protocol import ResourceRepositoryProtocol
from agentclaw.community.core.resources.service import ResourceService


class ResourceServiceFactory:
    """Mints :class:`ResourceService` instances bound to a specific bot.

    Constructs the slim ``core/resources/service.py`` ``ResourceService``
    (``bot_id`` + ``repository`` only). ``bot_repo`` is **not** a
    dependency here: the only ``_bot_repo`` consumer is ``upload_file``
    on the *legacy* ``core/resources/services/resource_service.py``
    class, reached via the legacy path — never through this factory.
    """

    @inject
    def __init__(self, repository: ResourceRepositoryProtocol) -> None:
        self._repository = repository

    def create(self, *, bot_id: str) -> ResourceService:
        return ResourceService(bot_id=bot_id, repository=self._repository)
