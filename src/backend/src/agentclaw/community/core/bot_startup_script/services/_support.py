"""Which bots can run a startup script, and why not (issue #926).

One question: **does this bot's container get its start command from**
``_build_create_bot_payload``? That is the only place the stored script is
resolved and appended, so a bot provisioned any other way would store a script
that never runs — the silent no-op this whole check exists to prevent.

Two provisioners answer no, and both are real rather than hypothetical:

* **teclaw** — ``TeclawProvisionService`` explicitly skips
  ``DeviceService.apply_device``, so the container never gets a
  ``deploy_config`` for a start command to ride on.
* **desktop** — ``DesktopBotService`` builds its hook by calling
  ``_get_start_cmd`` *directly* (``desktop_bot_service.py``), bypassing the
  payload builder where the script is resolved.

The answer must be the same for every caller. It is computed here, once, so a
read (``supported`` / ``unsupported_reason``) and a write (409 or not) cannot
disagree — an earlier version gated only the write, and ``GET`` went on
reporting ``supported: true`` for a bot whose next ``PUT`` was certain to fail.

Support is otherwise a property of the bot's **engine**, asked of the engine's
own authority. Two things this deliberately does not do, both of which it used to:

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

A second case answers ``supported`` optimistically for the same reason, and is
worth naming because it is not a pre-existing-bot problem: a deployment
provisioning from a **``LOCAL``-type BaaS template** (a single-machine /
singlebox install). This one is not a backend gap — the payload is built and the
hook sent exactly as for any other bot, and BaaS then drops it:
``_device_service.py`` returns before dispatch when ``provider_type == "LOCAL"``,
deferring to a ``container_ready`` callback that completes the publish record and
never runs the hook.

It is not refused here because it is not visible here. ``provider_type`` is
derived from the BaaS *template's* configured type, which is deployment data; the
bot record carries no field distinguishing a LOCAL-templated install from any
other baas-backed one. Refusing it would mean asking BaaS about the template on
every support check — reintroducing exactly the live-state dependency, and the
third "we could not find out" state, that this function was rewritten to remove.
Closing it properly belongs on the BaaS side, where the LOCAL branch already
knows it is skipping the hook.
"""
from __future__ import annotations

from typing import Any, Callable

#: Kept byte-identical to the re-export in ``api/bot_startup_script_service.py``,
#: which the HTTP adapter branches on (core must not import that layer).
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"


#: The bot type whose start command is built outside ``_build_create_bot_payload``.
#:
#: A literal, matching how the rest of the codebase asks this question
#: (``bot.get("bot_type") == "desktop"`` in ``skill_center`` and the bots
#: router). Unlike the engine test there is no service that owns the definition;
#: if one appears, this is the single place to point at it.
_DESKTOP_BOT_TYPE = "desktop"


def resolve_support(
    bot: dict[str, Any], is_teclaw: Callable[[str | None], bool]
) -> tuple[str, str]:
    """Return ``(state, reason)`` — SUPPORTED or UNSUPPORTED.

    Args:
        bot: The bot record.
        is_teclaw: The canonical engine test, passed in rather than imported so
            this stays a pure function of its arguments and the caller owns the
            (cycle-sensitive) dependency.
    """
    if is_teclaw(bot.get("active_engine")):
        return UNSUPPORTED, "teclaw bots are provisioned without a start sequence"
    if bot.get("bot_type") == _DESKTOP_BOT_TYPE:
        return (
            UNSUPPORTED,
            "desktop bots build their start command outside the shared sequence",
        )
    return SUPPORTED, ""
