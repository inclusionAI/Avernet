"""Which bots can run a startup script, and why not (issue #926).

Support keys on the **container provider**, never on the bot type. Personal and
service bots share one allocator (``_allocate_via_baas`` accepts both) and one
payload builder, so both reach the platform start sequence the script is
appended to; what differs is which provider the bot's template resolved to.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.devices.services.device_service import (
    BAAS_DEVICE_PROVIDER,
    LOCAL_DEVICE_PROVIDER,
)

#: Providers whose containers are built by ``_build_create_bot_payload``, and
#: therefore carry the start command the script is appended to. ``local`` is
#: here because ``LocalDeviceService`` calls that same builder
#: (local_device_service.py:252) — it delivers the script like ``baas`` does.
_PROVIDERS_WITH_A_START_SEQUENCE = frozenset(
    {BAAS_DEVICE_PROVIDER, LOCAL_DEVICE_PROVIDER}
)


def resolve_support(
    bot: dict[str, Any], device_provider: str | None
) -> tuple[bool, str]:
    """Return ``(supported, reason)`` for a bot; ``reason`` is "" when supported.

    Two real deployments cannot run one, and neither is hypothetical:

    * **teclaw** — its container is provisioned by ``TeclawProvisionService``,
      which explicitly skips ``DeviceService.apply_device``, so it never gets a
      ``deploy_config`` for a start command to ride on.
    * **anything not on the baas device provider** — bots created before the
      BaaS rollout talk to ARCA directly and never build an
      ``after_create_cmd_hook`` through ``_build_create_bot_payload``.
    """
    from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
        TECLAW_DEVICE_PROVIDER,
    )

    engine = str(bot.get("active_engine") or "").strip().lower()
    if engine == TECLAW_DEVICE_PROVIDER:
        return False, "teclaw bots are provisioned without a start sequence"

    if device_provider is None:
        # No binding yet — a bot is PENDING between create and its first start,
        # and that is precisely when an owner wants to attach a script. The
        # provider is not knowable here, and refusing would block the main use
        # case; every non-teclaw bot created today is baas-backed.
        return True, ""

    if device_provider not in _PROVIDERS_WITH_A_START_SEQUENCE:
        return (
            False,
            f"bots on the {device_provider!r} device provider have no start sequence",
        )

    return True, ""
