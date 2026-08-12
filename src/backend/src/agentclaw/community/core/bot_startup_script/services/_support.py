"""Which bots can run a startup script, and why not (issue #926).

Support is a property of the bot's **engine**, asked of the engine's own
authority. Two things this deliberately does not do, both of which it used to:

* **It does not compare engine strings.** ``TeclawProvisionService.is_teclaw``
  is the single definition of "runs in a teclaw container", it keys on a
  configured set rather than a literal, and ``BotService.is_teclaw_bot``
  delegates to it for exactly this reason. A second hand-rolled
  ``active_engine == "teclaw"`` here would be a divergent copy that a new
  teclaw-like engine would have to remember to update.

* **It does not look at the bot's live container.** Support is answered from
  the bot record alone, so it is the same answer before the first start, during
  a restart, and while a binding lookup is failing. The previous version keyed
  on the resolved ``device_provider`` and needed a third "we could not find
  out" state to cover the lookup failing — a state that made a transient blip
  in an unrelated read look like a verdict about the bot.

The cost of dropping the provider check is stated rather than hidden: a legacy
ARCA-direct bot — one created before the BaaS rollout, whose container is not
built through ``_build_create_bot_payload`` — is no longer refused, so its owner
can store a script that will not run. Every bot created today is baas-backed
unless it is teclaw, so this affects pre-existing bots only, and the trade buys
an answer that does not depend on live state.
"""
from __future__ import annotations

from typing import Any, Callable

#: Kept byte-identical to the re-export in ``api/bot_startup_script_service.py``,
#: which the HTTP adapter branches on (core must not import that layer).
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"


def resolve_support(
    bot: dict[str, Any], is_teclaw: Callable[[str | None], bool]
) -> tuple[str, str]:
    """Return ``(state, reason)`` — SUPPORTED or UNSUPPORTED.

    Args:
        bot: The bot record.
        is_teclaw: The canonical engine test, passed in rather than imported so
            this stays a pure function of its arguments and the caller owns the
            (cycle-sensitive) dependency.

    A teclaw bot's container is provisioned by ``TeclawProvisionService``, which
    explicitly skips ``DeviceService.apply_device`` — so it never receives a
    ``deploy_config``, and there is no start command for a script to ride on.
    """
    if is_teclaw(bot.get("active_engine")):
        return UNSUPPORTED, "teclaw bots are provisioned without a start sequence"
    return SUPPORTED, ""
