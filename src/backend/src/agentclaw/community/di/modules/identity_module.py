"""IdentityModule — production singleton for the identity service.

``IdentityService.__init__`` uses ``@inject`` to receive
``WorkspacePathFactory``, ``BotPublishRepositoryProtocol`` and
``BaasService``; a ``configure`` self-binding is enough for the
injector to construct it.

The ``IdentityService`` import is deferred to ``configure`` time so
that loading this module file does not eagerly pull
``service_bot/__init__.py`` into the early DI bootstrap (which would
tangle the ``bot_service ↔ publish_flow_service`` import cycle).
"""
from __future__ import annotations

from injector import Binder, Module, singleton


class IdentityModule(Module):
    """Production bindings for the identity service."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.core.services.identity import IdentityService

        binder.bind(IdentityService, to=IdentityService, scope=singleton)
