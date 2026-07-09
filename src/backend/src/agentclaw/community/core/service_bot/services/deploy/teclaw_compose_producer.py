"""``TeclawComposeProducer`` — the teclaw external build producer.

Concrete :class:`ExternalComposeProducer` for the teclaw engine. It adds exactly
one engine-specific behavior on top of the base: fetching teclaw's opaque
``engine_ext`` at build time via the :class:`EngineExtClient` plugin. Everything
else — compose, freeze, pin — is the engine-agnostic base flow.

``engine_ext`` is **opaque**: the payload teclaw returns is frozen into the
version and later returned to the engine verbatim; the backend never interprets
or branches on its contents (Rule: engine-ext opacity, guarded in Task 17).

🔒 The *real* teclaw fetch lives in the prod ``EngineExtClient`` (Task 15, blocked
on the engine-owner handshake). In dev/test the local Noop returns ``{}`` and a
Mock injects fixed JSON — so this producer is fully exercised now.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.service_bot.services.deploy.external_compose_producer import (
    ConfigComposerLike,
    ExternalComposeProducer,
)
from agentclaw.community.plugin_api.engine_ext_client import EngineExtClient


class TeclawComposeProducer(ExternalComposeProducer):
    """External compose producer that sources ``engine_ext`` from teclaw."""

    def __init__(
        self,
        composer: ConfigComposerLike,
        engine_ext_client: EngineExtClient,
    ) -> None:
        super().__init__(composer=composer)
        self._engine_ext_client = engine_ext_client

    def _fetch_engine_ext(self, bot: dict[str, Any]) -> dict[str, Any]:
        """Fetch teclaw's opaque ``engine_ext`` via the plugin (carried verbatim).

        The teclaw endpoint / field-mapping detail lives behind the
        :class:`EngineExtClient` plugin (prod impl in Task 15); here we only
        delegate and pass the result through untouched.
        """
        return self._engine_ext_client.fetch(bot)
