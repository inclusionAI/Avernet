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


#: Kept byte-identical to the re-export in ``api/bot_startup_script_service.py``,
#: which the HTTP adapter branches on (core must not import that layer).
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNKNOWN = "unknown"


def resolve_support(
    bot: dict[str, Any], device_provider: str | None
) -> tuple[str, str]:
    """Return ``(state, reason)`` — one of SUPPORTED / UNSUPPORTED / UNKNOWN.

    Three states rather than two because "this bot can never run a script"
    and "we could not find out right now" call for different answers: the
    first is a permanent refusal, the second is retryable and must not tell
    an owner their healthy bot is unsupported.

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
        return UNSUPPORTED, "teclaw bots are provisioned without a start sequence"

    if device_provider is None:
        # Two different situations reach here and they must not be conflated.
        #
        # A bot with no binding at all is PENDING between create and its first
        # start — precisely when an owner wants to attach a script, and every
        # non-teclaw bot created today is baas-backed, so allow it.
        #
        # A bot that HAS a binding_id but arrived without a provider is a
        # swallowed lookup (``get_bot`` wraps that read in a warning-only
        # try/except). Allowing it would let a legacy arca bot store a script
        # that never runs; refusing it permanently would tell the owner of a
        # healthy bot that it can never run one. Neither — say so, and let the
        # caller retry.
        if bot.get("binding_id"):
            return UNKNOWN, "the bot's device provider could not be determined"
        return SUPPORTED, ""

    if device_provider not in _PROVIDERS_WITH_A_START_SEQUENCE:
        return (
            UNSUPPORTED,
            f"bots on the {device_provider!r} device provider have no start sequence",
        )

    return SUPPORTED, ""
