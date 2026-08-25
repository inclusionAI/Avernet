"""``DeployConfigComposer`` — the swappable "shape the create-bot payload" strategy.

Three fields of the BaaS ``deploy_config`` describe the *container this
deployment runs*, and nothing else in the payload does:

* ``after_create_cmd_hook`` — the shell that starts the bot inside the image,
* ``mount_points`` — the directories mounted into it,
* ``storage`` — the NAS volume attached to it (absent ⇒ none).

The managed deployment's answers are baked into its bot image: a four-step
chain over ``/home/admin/bin/*.sh``, three NAS mounts, always a storage block.
The ACK/ECI deployment runs the open-source engine image on managed
Kubernetes, where the image carries its own entrypoint and the storage
substrate is object storage — so its answers are not the managed ones with
different arguments, they are different answers.

A composer owns all three together because they co-vary: they are one
description of one runtime. Which composer runs is a deployment's choice, made
once in the composition root from validated config (Rule 14) — never branched
on per request.

Like ``DeployArtifactProducer`` next door, these are **core strategies**, not
plugins: they compose strings and value objects and cross no boundary, so they
need no ``local``/``prod`` split.

``BaasService`` keeps everything a composer must not have to know: the
``nas_mount`` whitelist (already resolved into ``BotDeployContext``), outbound
rules, resource specs, env/image overrides, and the per-bot startup script
(issue #926) — which it appends *after* the composer's chain, so a bot's stored
script runs on every deployment without each composer re-implementing the
exit-status wrapper that makes it safe.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agentclaw.community.core.service_bot.services.deploy.deploy_models import (
    MountPointEntry,
    Storage,
)

__all__ = [
    "BotDeployContext",
    "DeployConfigComposer",
]


@dataclass(frozen=True)
class BotDeployContext:
    """Everything a composer is told about the bot being provisioned.

    One context for all three methods rather than three argument lists: the
    three outputs describe one container, and a field only one of them reads
    today (``version``) is one the next composer may well read in another.

    ``mount_home_dir_storage`` arrives **already resolved**. It is a
    ``nas_mount`` whitelist decision, and a whitelist is a rollout mechanism —
    a service concern, not a property of any deployment's container. Resolving
    it once upstream also means one whitelist read per payload instead of the
    three the old call chain could make.
    """

    bot_id: str
    owner_id: str
    entity_id: str
    entity_type: str
    bot_type: str
    engine: str
    #: Previous version's data directory to migrate from; ``""`` for a bot with
    #: no predecessor (personal bots, service-bot drafts).
    migration_path: str
    #: Whether this bot mounts its NAS home directory rather than the sessions
    #: directory. Resolved from the ``nas_mount`` whitelist by ``BaasService``.
    mount_home_dir_storage: bool
    #: Publish stage; ``None`` when the caller has none to declare.
    stage: Optional[str] = None
    version: str = "1"
    #: Caller-supplied extra NAS mount, mounted at the same path it names.
    mount_path: Optional[str] = None
    ext_info: Optional[Dict[str, Any]] = None


class DeployConfigComposer(abc.ABC):
    """Composes the runtime-specific fields of one create-bot ``deploy_config``."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable identifier, matching the config value that selects it."""

    @abc.abstractmethod
    def build_start_command(self, ctx: BotDeployContext) -> str:
        """The platform boot chain for ``after_create_cmd_hook``.

        Returns the command that starts the bot inside the container. The
        per-bot startup script (issue #926) is **not** this method's concern —
        ``BaasService`` appends it to whatever is returned here.

        ``{token}`` and ``{client_id}`` may be left in the returned string as
        literal placeholders: BaaS substitutes them at dispatch time
        (``_safe_format_hook``), ``client_id`` being the device UUID, which the
        backend cannot know at compose time.
        """

    @abc.abstractmethod
    def build_mount_points(self, ctx: BotDeployContext) -> list[MountPointEntry]:
        """The directories mounted into the container, in payload order."""

    @abc.abstractmethod
    def build_storage(self, ctx: BotDeployContext) -> Storage | None:
        """The storage volume for this bot, or ``None`` to attach none.

        ``None`` drops the ``storage`` key from the payload entirely
        (``BotDeployConfig.to_dict``) — which is what a runtime that gives the
        container its own disk should send, not an empty or zero-quota block.
        """
