"""The bot gate the operator surfaces share.

The public engine-runtime surfaces hand a caller a channel onto a bot's
device: the sessions, engine, models and approvals groups
(``adapters/http/openapi_v1/engine_runtime/`` — gated through
``gating.resolve_operable_bot``) forward over HTTP, and the connection
endpoint (:mod:`agentclaw.community.core.engine_runtime.connection`) publishes
a WebSocket whose ``hello`` advertises the ``sessions.*`` and
``exec.approvals`` methods and grants ``operator.admin``. These are *operator*
channels: whoever holds them reaches device-wide state, including every
session on the addressed device.

They must therefore agree on two questions — which bots they serve, and who
may hold the channel — so both rules live here, once, rather than as
constants restated at each gate.

**Who may operate.** The bot's owner, or a collaborator at
:attr:`PermissionLevel.MEMBER` or above — the same bar
``DeviceService.get_device_connection`` applies to the internal operator
channel. Public visibility grants nothing: a public bot's audience converses
with it over the chat path; operating it is its team's. Anyone else is
answered with the same masked 404 as a bot that does not exist
(``BotNotFoundError``), because anything distinguishable confirms the bot
exists. A failed collaborator lookup refuses (fail closed): reading an
unavailable collaborator table as "no collaborators" would admit a stranger
at exactly the moment the check meant to stop them could not run.

An operator channel is device-wide *by contract*: the engine's session
collection is not scoped per caller in practice — ``GET /api/sessions``
accepts a ``user_id`` query parameter, but the engine ports drop it
(``plugins/openclaw/_session.py``, ``plugins/claude_code/_session.py``), so a
per-user filter upstream would be a silent no-op rather than isolation. An
admitted operator therefore sees every session on the addressed runtime,
including ones end users' chats created; the published docs say so plainly.

**Which bots.** ``personal`` and ``service`` — the allowlist. Anything else —
an empty type, a type this build has never heard of — is refused rather than
assumed personal.

**Which stages.** A ``service`` bot has up to three long-lived runtimes, and
the separation is *storage*, not an id scheme: the pre-publication draft
binds on ``ac_bots.binding_id``, while the verify/online runtimes publishing
produces bind only in ``ac_bot_publish.ext.binding.{verify,online}`` (the
records share the bot's own ``publish_bot_id``; no ``…pub…`` id is ever
written). A ``personal`` bot has only its own workspace runtime — the
``draft`` stage — so naming any other stage for one is refused as a stage
with no live runtime.
"""

from __future__ import annotations

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRole,
    PermissionLevel,
)
from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: The bot types an operator surface may serve. Necessary but **not**
#: sufficient — the caller must also pass :func:`require_bot_operator`.
SUPPORTED_BOT_TYPES = frozenset({"personal", "service"})

#: The least collaborator level that holds an operator channel. One bar for
#: every operation on the surface — reads, writes and the socket alike —
#: matching the internal device-connection's. A finer per-operation split
#: would be new policy this platform has nowhere else; if one is ever wanted,
#: this is the seam to add it at.
OPERATOR_LEVEL = PermissionLevel.MEMBER

#: ``ac_bot_collaborator.role`` values → comparable levels. The owner never
#: has a row (adding one raises), so the mapping covers collaborators only.
_ROLE_LEVELS = {
    CollaboratorRole.ADMIN.value: PermissionLevel.ADMIN,
    CollaboratorRole.MEMBER.value: PermissionLevel.MEMBER,
}


def resolve_operator_level(
    collaborator_repo: CollaboratorRepositoryProtocol,
    *,
    bot_pk: int,
    caller_id: str,
    owner_id: str,
) -> PermissionLevel:
    """The caller's level on this bot: OWNER, their collaborator level, or NONE.

    ``owner_id`` must be the *resolved* owner — the record's, not the
    request's — and ``bot_pk`` the primary key ownership was proven against;
    ``bot_id`` alone is not unique across owners. A lookup failure returns
    ``NONE`` (fail closed) and logs: this feeds a refusal, so the direction of
    the guess decides what a database blip does.

    Synchronous — one indexed read, and only when the caller is not the
    owner. Callers on an event loop run it in a worker thread with the rest
    of their resolution.
    """
    if caller_id == owner_id:
        return PermissionLevel.OWNER
    try:
        role = collaborator_repo.get_user_role(bot_pk, caller_id, get_current_env())
    except Exception:
        logger.exception(
            "[engine_runtime] collaborator lookup failed for bot_pk=%s; "
            "refusing the caller",
            bot_pk,
        )
        return PermissionLevel.NONE
    return _ROLE_LEVELS.get(str(role or ""), PermissionLevel.NONE)


def require_bot_operator(
    collaborator_repo: CollaboratorRepositoryProtocol,
    *,
    bot_pk: int,
    bot_id: str,
    caller_id: str,
    owner_id: str,
) -> None:
    """Refuse a caller who is not this bot's operator, before any device work.

    Raises the same ``BotNotFoundError`` an absent bot does, so a refused
    non-operator cannot tell a bot they may not operate from one that does not
    exist. Both ids go to the log at the point of refusal — the response
    cannot carry them.
    """
    level = resolve_operator_level(
        collaborator_repo, bot_pk=bot_pk, caller_id=caller_id, owner_id=owner_id
    )
    if level < OPERATOR_LEVEL:
        # ``%r`` on the caller id deliberately: this branch runs only for a
        # value the server refused, so quoting keeps a forged multi-line id
        # from poisoning the refusal audit trail.
        logger.warning(
            "[engine_runtime] caller %r is not an operator of bot=%s owner=%s",
            caller_id,
            bot_id,
            owner_id,
        )
        raise BotNotFoundError(f"bot {bot_id} not found")


def require_operable_bot(bot_type: str, *, surface: str) -> None:
    """Reject bot types the operator surfaces do not serve.

    Callers run this after the operator adjudication (so a stranger learns
    nothing, not even the type) and before composing a socket or forwarding a
    request. ``surface`` names what the caller serves ("sessions",
    "connections") so the refusal reads the same as it always has on each
    surface; the adapter maps it to a 501 — what the surface cannot serve,
    rather than something the caller may retry or fix.
    """
    if bot_type not in SUPPORTED_BOT_TYPES:
        raise EngineBotTypeNotSupportedError(
            f"{surface} are not served for bot_type={bot_type!r}"
        )


__all__ = [
    "OPERATOR_LEVEL",
    "SUPPORTED_BOT_TYPES",
    "require_bot_operator",
    "require_operable_bot",
    "resolve_operator_level",
]
