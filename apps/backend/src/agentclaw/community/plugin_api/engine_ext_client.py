"""``EngineExtClient`` — fetch a bot's opaque, engine-owned ``engine_ext`` payload.

At the publish **build** phase the external compose producer
(``TeclawComposeProducer``) calls this to obtain the engine's free-form JSON
(pointers/metadata — e.g. MEMORY.md / IDENTITY.md references) and freeze it into
the version artifact. The payload is **opaque**: the backend stores / freezes /
returns it verbatim and must never interpret or branch on its contents.

This is the only new plugin in the external-container design:

* ``local`` → Noop (returns ``{}``; no external engine in dev).
* ``prod`` → engine-specific HTTP client (teclaw). Placeholder until the
  engine-owner handshake lands (Task 15).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class EngineExtClient(Plugin, Protocol):
    """Fetch the opaque ``engine_ext`` payload for a bot from its engine."""

    def fetch(self, bot: dict[str, Any]) -> dict[str, Any]:
        """Return the engine's opaque free-form JSON for ``bot``.

        Args:
            bot: Bot info dict (``bot_id`` / ``entity_id`` / ``engine_type`` …).

        Returns:
            An **opaque**, engine-owned mapping (pointers / metadata). It MUST be
            treated as a black box: stored, frozen with the version, and returned
            to the engine verbatim — never interpreted. ``dict[str, Any]`` is the
            honest type precisely because the shape is engine-defined and the
            backend does not (and must not) know it. Empty mapping when the engine
            has nothing to contribute.
        """
        ...
