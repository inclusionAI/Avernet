"""``AckDeployConfigComposer`` — the open-source engine image on ACK/ECI.

The ACK/ECI deployment runs the open-source engine image on managed
Kubernetes, where the image carries its own entrypoint — so the start command
is a single ``nohup`` invocation rather than the managed image's four-step
chain. ``{token}`` and ``{client_id}`` are left as literal placeholders that
BaaS substitutes at dispatch time (``_safe_format_hook``), ``client_id`` being
the device UUID.

``bot_id`` and ``owner_id`` are resolved from :class:`BotDeployContext` at
compose time — unlike ``token`` / ``client_id``, the backend knows them.

``BaasService`` appends the per-bot startup script (issue #926) *after*
``build_start_command``'s return value, so this method must not include it.

See ``ManagedDeployConfigComposer`` for how the managed deployment answers the
same three questions.
"""
from __future__ import annotations

import shlex

from agentclaw.community.core.service_bot.services.deploy.deploy_config_composer import (
    BotDeployContext,
    DeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.deploy.deploy_models import (
    MountPointEntry,
    Storage,
    StorageType,
)
from agentclaw.community.kernel.deploy_runtime import DeployRuntime

class AckDeployConfigComposer(DeployConfigComposer):
    """Compose the create-bot payload for the open-source engine image on ACK/ECI."""

    @property
    def name(self) -> DeployRuntime:
        return DeployRuntime.ACK

    def build_start_command(self, ctx: BotDeployContext) -> str:
        """The single ``nohup`` that starts the engine inside the ACK image.

        ``{token}`` and ``{client_id}`` remain literal placeholders for BaaS
        to substitute at dispatch; ``bot_id`` and ``owner_id`` are filled from
        the context.
        """
        initial_cwd = ""
        if ctx.engine == "claude_code" and ctx.claude_code_default_cwd is not None:
            from agentclaw.community.core.workspace.claude_code_config import validate_claude_code_cwd
            initial_cwd = " --claude-initial-cwd " + shlex.quote(
                validate_claude_code_cwd(ctx.claude_code_default_cwd))
        command = (
            f"nohup start_service.sh --token {{token}} "
            f"--client_id {{client_id}} --engine {ctx.engine} "
            f"--bot_id {ctx.bot_id} --owner_id {ctx.owner_id}{initial_cwd} "
            ">> /home/admin/start.log 2>&1"
        )
        return "su admin -c " + shlex.quote(command)

    def build_mount_points(self, ctx: BotDeployContext) -> list[MountPointEntry]:
        """No bind-mounts: the ACK pod's volumes come from the ``storage``
        block, not from pre-existing shared directories."""
        return []

    def build_storage(self, ctx: BotDeployContext) -> Storage | None:
        """A NAS volume at ``/home/admin`` for the bot's persistent state.

        ``storage_id`` is per-bot — it carries the ``bot_id`` so BaaS can
        find the same volume again on the next start.
        """
        return Storage(
            type=StorageType.NAS,
            path="/home/admin",
            storage_id=ctx.bot_id,
            quota="1Gi",
            permission="0777",
        )
