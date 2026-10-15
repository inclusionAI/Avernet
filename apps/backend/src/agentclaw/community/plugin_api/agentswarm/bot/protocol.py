"""In-process seam for driving avernet-kind bots through the host platform.

The subagent's job (provision a transient bot persona, drive it to READY,
chat one objective, delete it) is platform work this backend cannot do
itself — the binding lives host-side, not here: the community host
adapter (``plugins/community/agentswarm/host.py`` — the
create-with-manifest flow for provisioning, the grt_chat engine
transport for the chat leg) is bound by the ``AgentSwarmModule.bot_platform``
DI provider (``di/modules/agentswarm``) in the composition root's base
module list. Constructing the adapter requires the configured host owner
(``agentswarm.host.owner_id`` — the account swarm-created bots belong to,
no service-account fallback); the provider refuses construction with a
precise ``AgentSwarmConfigError`` when it is blank, and profiles whose
platform-side dependencies that provider cannot resolve keep the designed
failure — avernet-kind spawns fail
clear at the factory — and
the swarm defines the seam and the
composition root binds the host's in-process BotPlatform adapter; nothing
in agentswarm opens a socket, and a missing BotPlatform binding means
avernet-kind spawns fail clear at the factory (the failure mode of the
removed HTTP implementation was hitting a 404 endpoint).

The seam carries a structured create request, not a platform document:
a BotCreateSpec (name, description, instructions, model, metadata) is the
product's create vocabulary (bot_name/bot_desc) in semantic form, and
translating it into the real create request — engine, cluster, bot_type
and space filled from the adapter's own deployment knowledge, the
five-category `bot_config_manifest` document (see docs/bot-config-manifest/)
composed and validated on the platform side — is the adapter's job.
"Adapters translate, core stays transport-agnostic": agentswarm never
sees or guesses the document format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NewType, Protocol, runtime_checkable

BotId = NewType("BotId", str)
IdempotencyKey = NewType("IdempotencyKey", str)


@dataclass(frozen=True)
class BotCreateSpec:
    """Structured bot create request — what crosses the BotPlatform seam.

    The product creation vocabulary (bot_name/bot_desc) plus the
    per-agent provision parameters (engine/bot_type/identity_file)
    the catalog spec chose. The adapter derives cluster from engine
    (a bijection, not a config) and composes the manifest document.
    """
    name: str
    description: str = ""
    instructions: str = ""
    model: str | None = None
    engine: str = ""
    bot_type: str = "personal"
    identity_file: str = "SOUL.md"
    metadata: dict[str, str] = field(default_factory=dict)






@runtime_checkable
class BotPlatform(Protocol):

    def create_bot(self, *, spec: BotCreateSpec,
                   idempotency_key: IdempotencyKey,
                   owner_id: str = "") -> BotId:
        """Provision a transient bot for the persona described by spec;
        returns a non-empty bot_id or raises when the platform cannot
        accept it. The adapter translates spec into the product create
        request and composes the manifest document. ``owner_id`` is the
        authenticated caller the bot is provisioned under."""
        ...

    def bot_status(self, bot_id: BotId, owner_id: str = "") -> str:
        """Current BotState-like status string for the bot, scoped to the
        provisioning owner."""
        ...

    def delete_bot(self, bot_id: BotId, owner_id: str = "") -> bool:
        """Best-effort removal of the transient bot."""
        ...

    def chat(self, bot_id: BotId, message: str, owner_id: str = "") -> str:
        """Send one objective and return the bot's raw reply text; the
        caller owns parsing. Non-2xx-equivalent failures raise. The wait
        is finite: a hung engine transport surfaces as a raised error
        (RuntimeError-family, e.g. the community adapter's ChatTimeoutError)
        rather than an unbounded block — the caller offloads this call to
        a thread, which must not be held hostage forever."""
        ...