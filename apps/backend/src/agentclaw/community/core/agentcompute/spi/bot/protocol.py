"""In-process seam for driving avernet-kind bots through the host platform.

The subagent's job (provision a transient bot persona, drive it to READY,
chat one objective, delete it) is platform work this backend cannot do
itself — the binding lives host-side, not here: the community host
adapter (``plugins/community/agentcompute_platform.py`` — the
create-with-manifest flow for provisioning, the grt_chat engine
transport for the chat leg) is bound by ``CommunityAgentComputePlatformModule``
in the community column and the singlebox branch of the profile
composition. Profiles whose column does not install the binding keep the
designed failure — avernet-kind spawns fail clear at the factory — and
the swarm defines the seam and the
composition root binds the host's in-process BotPlatform adapter; nothing
in agentcompute opens a socket, and a missing BotPlatform binding means
avernet-kind spawns fail clear at the factory (the failure mode of the
removed HTTP implementation was hitting a 404 endpoint).

The seam carries a structured create request, not a platform document:
a BotCreateSpec (name, description, instructions, model, metadata) is the
product's create vocabulary (bot_name/bot_desc) in semantic form, and
translating it into the real create request — engine, cluster, bot_type
and space filled from the adapter's own deployment knowledge, the
five-category `bot_config_manifest` document (see docs/bot-config-manifest/)
composed and validated on the platform side — is the adapter's job.
"Adapters translate, core stays transport-agnostic": agentcompute never
sees or guesses the document format.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    BotCreateSpec, BotId, IdempotencyKey,
)


@runtime_checkable
class BotPlatform(Protocol):

    def create_bot(self, *, spec: BotCreateSpec,
                   idempotency_key: IdempotencyKey) -> BotId:
        """Provision a transient bot for the persona described by spec;
        returns a non-empty bot_id or raises when the platform cannot
        accept it. The adapter translates spec into the product create
        request and composes the manifest document."""
        ...

    def bot_status(self, bot_id: BotId) -> str:
        """Current BotState-like status string for the bot."""
        ...

    def delete_bot(self, bot_id: BotId) -> bool:
        """Best-effort removal of the transient bot."""
        ...

    def chat(self, bot_id: BotId, message: str) -> str:
        """Send one objective and return the bot's raw reply text; the
        caller owns parsing. Non-2xx-equivalent failures raise."""
        ...