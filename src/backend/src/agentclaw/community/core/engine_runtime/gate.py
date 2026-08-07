"""The bot gate the operator surfaces share.

Two public surfaces hand a caller a channel onto their bot's device: the
sessions group (``adapters/http/openapi_v1/.../sessions/router.py``) wraps the
engine's ``/api/sessions`` routes over HTTP, and the connection endpoint
(:mod:`agentclaw.community.core.engine_runtime.connection`) publishes a
WebSocket whose ``hello`` advertises the ``sessions.*`` and ``exec.approvals``
methods and grants ``operator.admin``. Both are *operator* channels: whoever
holds them reaches every session on the device.

They must therefore agree on which bots they serve — a bot refused a session
list but handed an operator socket is a 501 on the front door with the window
left open — so the rule lives here, once, rather than as a constant restated at
each gate. :func:`bot_is_shared <agentclaw.community.core.engine_runtime.\
sharing.bot_is_shared>` is the same idea for the sharing half of the check.

**Which bots pass.** The hazard is *multi-caller*, not bot type: the engine has
no tenant axis and its session collection is not scoped per caller, so the gate
admits exactly the bots whose device only the owner reaches.

- A ``personal`` bot — provided it is unshared. ``personal`` is single-caller
  only by default: the bot can be made public and a coding app can take
  collaborators, and ``ExpertChatService`` then creates those callers' sessions
  on this same binding.
- A ``service`` bot — for its **draft** device, and again only unshared. Both
  surfaces address the device by ``bot_id``: the connection service reads the
  binding via ``get_active_by_bot_and_owner`` and the sessions group forwards
  with ``draft_device=True``, both of which resolve ``ac_bots.binding_id`` —
  the pre-publication draft. The verify/online runtimes publishing produces are
  bound under ``publish_bot_id`` (``source_bot_id + "pub" + version``) on the
  publish records, so a ``bot_id``-addressed call structurally cannot reach the
  multi-caller published device. The unshared draft binding holds only the
  owner's own sessions, which is the same exposure as a private personal bot.
  A surface that instead resolves the *published* runtime (the relay's default
  for service bots) must not be opened with this gate.

Anything else — an empty type, a type this build has never heard of — is
refused rather than assumed personal: the gate is an allowlist.
"""

from __future__ import annotations

from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
)

#: The bot types an operator surface may serve. Necessary but **not**
#: sufficient — see :func:`require_operable_bot`.
SUPPORTED_BOT_TYPES = frozenset({"personal", "service"})


def require_operable_bot(bot_type: str, *, is_shared: bool, surface: str) -> None:
    """Reject bots more than one caller reaches, before any device is touched.

    Callers run this before composing a socket or forwarding a request — a
    filter applied to what the device returned would already have fetched
    every caller's sessions.

    ``surface`` names what the caller serves ("sessions", "connections") so
    the refusal reads the same as it always has on each surface. Both refusals
    are the same 501: what the surface cannot serve, rather than something the
    caller may retry or fix.
    """
    if bot_type not in SUPPORTED_BOT_TYPES:
        raise EngineBotTypeNotSupportedError(
            f"{surface} are not served for bot_type={bot_type!r}"
        )
    if is_shared:
        raise EngineBotTypeNotSupportedError(
            f"{surface} are not served for a shared bot: the engine's session "
            "collection is not scoped per caller"
        )


__all__ = ["SUPPORTED_BOT_TYPES", "require_operable_bot"]
