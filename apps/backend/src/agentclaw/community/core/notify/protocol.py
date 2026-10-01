"""Protocol for listing bots eligible for notification polling."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NotifyTarget:
    """A bot eligible for notification polling, with just enough context to
    route the engine probe by ``device_provider``.

    The probe URL / transport is resolved at probe time via
    ``DeviceContextResolver`` from ``owner_id`` (provider-driven: baas / arca /
    teclaw / local) — exactly the path chat / cron use. It is **not** derived
    from ``sandbox_id``; ``sandbox_id`` is kept only to echo the pre-existing
    response field and is intentionally not used for routing. Routing off
    ``sandbox_id`` was the root cause of desktop (BaaS) bots returning
    ``ENGINE_HTTP_500``: every bot was forced through the Arca proxypass
    formula regardless of provider.

    Attributes:
        bot_id: business bot id.
        bot_name: display name.
        owner_id: the binding's owner staff id — equals the requesting user
            for own bots, the real owner for collaborator bots. Passed to
            ``DeviceContextResolver`` so the right binding is resolved.
        sandbox_id: informational device/sandbox id echoed in the response
            (``""`` when unknown). NOT used for routing.
    """

    bot_id: str
    bot_name: str
    owner_id: str
    sandbox_id: str = ""


@runtime_checkable
class NotifyBotLister(Protocol):
    """Returns ``NotifyTarget``s eligible for notification polling."""

    def list_bot_mappings(self, user_id: str) -> list[NotifyTarget]:
        ...
