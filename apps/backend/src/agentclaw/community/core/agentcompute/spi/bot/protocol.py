"""In-process seam for driving avernet-kind bots through the host platform.

The subagent's job (provision a transient bot persona, drive it to READY,
chat one objective, delete it) is platform work this backend cannot do
itself — chat submission is engine-plane territory and the in-tree
surfaces are read-only queries. So the swarm defines the seam and the
composition root binds the host's in-process BotPlatform adapter; nothing
in agentcompute opens a socket, and a missing BotPlatform binding means
avernet-kind spawns fail clear at the factory (the failure mode of the
removed HTTP implementation was hitting a 404 endpoint).

The seam carries semantic persona fields, not a platform document:
composing a manifest the product schema accepts (`bot_config_manifest`'s
five-category grammar, see docs/bot-config-manifest/) is the platform
adapter's translation job — "adapters translate, core stays
transport-agnostic" — and agentcompute never guesses the document format.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BotPlatform(Protocol):

    def create_bot(self, *, name: str, instructions: str,
                   idempotency_key: str) -> str:
        """Provision a transient bot for the persona; returns bot_id,
        raises when the platform cannot accept it. The adapter builds the
        product-schema manifest document from these fields."""
        ...

    def bot_status(self, bot_id: str) -> str:
        """Current BotState-like status string for the bot."""
        ...

    def delete_bot(self, bot_id: str) -> bool:
        """Best-effort removal of the transient bot."""
        ...

    def chat(self, bot_id: str, message: str) -> str:
        """Send one objective and return the bot's raw reply text; the
        caller owns parsing. Non-2xx-equivalent failures raise."""
        ...