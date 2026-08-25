"""``AckDeployConfigComposer`` — the ACK/ECI deployment. **Unimplemented.**

This is the seam's second implementation, declared so the interface has the two
callers that justify it and so that deployment's work has a named place to
land. Its three methods raise; the deployment that selects it does not boot until they
are written.

That is the point. A composer that returned plausible-looking managed values
would produce bots that come up misconfigured on a runtime nobody has tested —
the failure would surface as a bot that starts and does not work, which is the
expensive kind. Raising means a deployment either has a real ACK composer or
fails loudly at the first create.

What each method owes its caller, for whoever implements it:

``build_start_command``
    One command that starts the open-source engine image, not the managed
    image's four-step chain — the ACK image carries its own entrypoint. May
    leave ``{token}`` / ``{client_id}`` as literal placeholders; BaaS
    substitutes them at dispatch (``_safe_format_hook``), ``client_id`` being
    the device UUID. Do **not** append the per-bot startup script (issue #926)
    here — ``BaasService`` does that to whatever this returns.

``build_mount_points``
    The ECI pod's mounts. On ACK these become K8s volumes (NAS or OSS) rather
    than the managed NAS bind-mounts, so the paths are the deployment's to
    choose. Note the OSS guidance that a mounted directory should stay under
    ~1000 files.

``build_storage``
    The bot's storage volume, or ``None`` to attach none — ``None`` drops the
    ``storage`` key from the payload entirely, which is the right answer for a
    pod that keeps its state elsewhere.

``BotDeployContext`` carries everything available at compose time; see
``ManagedDeployConfigComposer`` for how the managed deployment answers the same
three questions.
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.services.deploy.deploy_config_composer import (
    BotDeployContext,
    DeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.deploy.deploy_models import (
    MountPointEntry,
    Storage,
)
from agentclaw.community.kernel.deploy_runtime import DeployRuntime

_UNIMPLEMENTED = (
    "AckDeployConfigComposer.{method} is not implemented yet — the ACK/ECI "
    "deployment cannot create bots until it is. Set "
    "baas.deploy_runtime to 'managed' to use the managed bot image instead."
)


class AckDeployConfigComposer(DeployConfigComposer):
    """Compose the create-bot payload for the open-source image on ACK/ECI."""

    @property
    def name(self) -> DeployRuntime:
        return DeployRuntime.ACK

    def build_start_command(self, ctx: BotDeployContext) -> str:
        raise NotImplementedError(
            _UNIMPLEMENTED.format(method="build_start_command")
        )

    def build_mount_points(self, ctx: BotDeployContext) -> list[MountPointEntry]:
        raise NotImplementedError(
            _UNIMPLEMENTED.format(method="build_mount_points")
        )

    def build_storage(self, ctx: BotDeployContext) -> Storage | None:
        raise NotImplementedError(_UNIMPLEMENTED.format(method="build_storage"))
