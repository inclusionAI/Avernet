"""The grant an application writes for the bot it is creating.

One helper, shared by the three creations (``POST /openapi/v1/bots``, its
``with-manifest`` form, and ``POST /openapi/v1/bots/local``), so the rule is
written once:

> An application admitted to a creation is granted the bot it creates, as an
> ordinary bot grant, when the creation starts.

**Why at the start and not on completion.** For most of a creation's life there
is no bot record: the request answers ``202`` with authorization handles, and
the bot is created minutes later by a poll — a poll that is *bot-scoped* and
takes the own-bot grant dependency like every other bot operation. If the grant
were written on completion, the application could never reach the poll that
completes it. Writing it at the start makes the pending creation an ordinary
granted bot from the application's point of view, and the poll needs no
special case.

**Why this is not "inventing consent".** The application reached the creation
only under a live user-level delegation from the user — consent, at the
account level, to act as them where no bot is addressed. The creation grant
narrows that consent to one bot: the one the platform allocated, in this
request, for this user, at the application's asking. The row it writes is then
indistinguishable from one the user consented to by hand — listed on the bot,
withdrawable by its owner, swept when the bot is deleted.

**A human caller writes nothing here.** A person creating their own bot has
no application to grant, and an App riding along on a human's identity set is
a human request (see ``require_acting_caller``); nothing about their creation
changes.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.errors import (
    GrantNotResolvableError,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.log import get_logger

logger = get_logger()


def grant_the_creating_app(caller: ActingCaller, *, bot_id: str) -> None:
    """Grant ``caller``'s application the bot it is creating for the user.

    A no-op for a human caller. For an application it writes the bot grant for
    ``(app, bot_id, user, owner=user)`` — the creations are always the named
    user's own bot, so the owner is the delegating user by construction.

    Refuses rather than proceeds when the grant cannot be written: a bot the
    application created but can never reach is a worse outcome than a
    creation that did not start, and the caller can simply retry. The refusal
    is the same 404 every other unresolvable grant gets.
    """
    if not caller.is_application:
        return
    if caller.grants is None or caller.app_id is None:
        raise GrantNotResolvableError(
            "no grant writer for an application caller; the creation cannot be "
            "granted to the application"
        )
    caller.grants.grant_for_creation(
        bot_id=bot_id,
        user_id=caller.user_id,
        owner_id=caller.user_id,
        app_id=caller.app_id,
        app_name=caller.app_name or "",
    )


def withdraw_the_creation_grant(caller: ActingCaller, *, bot_id: str) -> None:
    """Best-effort withdrawal, for a creation that failed before it began.

    Called when the creation flow raises after the grant was written — a quota
    refusal, an invalid name, a Passport failure. The row would otherwise name
    a bot that will never exist. Never raises: the caller's real error is the
    one that must reach them, and a withdrawal that could not run leaves an
    inert row, not a live authorization.
    """
    if not caller.is_application or caller.grants is None or caller.app_id is None:
        return
    try:
        caller.grants.revoke(
            bot_id=bot_id,
            user_id=caller.user_id,
            owner_id=caller.user_id,
            app_id=caller.app_id,
        )
    except Exception:  # noqa: BLE001 — best effort, by contract
        logger.warning(
            "[bot_app_grant] could not withdraw the creation grant for "
            "app_id=%s bot=%s after the creation failed; the row is inert",
            caller.app_id,
            for_log(bot_id),
            exc_info=True,
        )


__all__ = ["grant_the_creating_app", "withdraw_the_creation_grant"]
