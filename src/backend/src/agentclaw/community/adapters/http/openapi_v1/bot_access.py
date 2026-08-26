"""The seam: may this caller do this to this bot, and what does that leave behind.

One dependency factory, built per operation from its :class:`Check` row and
attached by :class:`~...openapi_v1.authorization.PublicAPIRoute`. No handler
declares it and no handler can decline it.

**The check and the action read the same values, by construction.** The bot
comes from ``{bot_id}`` on the path and the owner from ``OwnerIdDep``, which is
the same dependency the handlers take — so there is no arrangement of query
parameters that lets a caller aim the check at one bot while the handler acts on
another. That failure is not hypothetical on this surface: it is what
``principal.py``'s ``require_granted_own_bot`` was written to make impossible,
and what the harness group still does today. Reading the wire in one place, once,
is the whole defence.

**Every failure refuses.** An unresolvable bot, an unreadable collaborator
table, an unexpected exception — all yield ``NONE`` and a masked 404. This is a
deliberate departure from the internal interceptor, which on the same failures
sets ``permission_skipped`` and *proceeds*
(``core/bot_collaborator/interceptor/collaborator.py:186``). That direction is
wrong for a gate: reading an unavailable collaborator table as "no collaborators"
admits a stranger at exactly the moment the check meant to stop them could not
run. The direction here matches ``core/engine_runtime/gate.py``, which says the
same thing about the same lookup.

**Edit lock is explicit.** A row that adds ``EDIT_LOCK`` checks the Bot lock
after its permission bar succeeds. Bots without collaborators pass without a
lock; member-management applications keep their session-level concurrency
model; all other collaborative Bots require the caller to hold the Bot lock.
Rows without ``EDIT_LOCK`` never consult the lock service.

**Audit is separate from permission.** They are two settings that cannot
disable each other: the level check runs before the handler, the record is
written after it, and ``_is_audited`` consults only the request method. The
internal interceptor couples them — ``persist_audit_log=False`` silently skips
the *lock* too — which is how a policy ends up being made by a flag name.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.access_log import (
    RESPONSE_STATUS_KEY,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    Check,
    ServiceChecked,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    BotAccessRefusedError,
    BotEditLockCheckError,
    BotEditLockRequiredError,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.api.member_management_capability import (
    MemberManagementCapabilityProtocol,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
    resolve_operable_permission_level,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Methods that observe rather than change, and so leave no audit record.
#:
#: Measured against the internal surface rather than assumed: of its 90
#: intercepted routes, all 36 ``GET`` routes disable the audit and all 54
#: non-``GET`` routes write one, with no exception in either direction. An
#: operation that reads but is spelled ``POST`` would be audited wrongly; none
#: is adjudicated here today, so no per-row override exists yet.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_check(rule: Check) -> Callable[..., AsyncIterator[None]]:
    """Build the dependency that enforces one operation's row.

    Enforcement only: the return value is not reachable from a handler, which is
    why handlers that need the resolved owner keep declaring ``OwnerIdDep``
    themselves. FastAPI caches a dependency per request, so that is the same
    resolution and not a second lookup.
    """

    async def gate(
        request: Request,
        bot_id: BotIdPath,
        caller_id: UserIdDep,
        owner_id: OwnerIdDep,
    ) -> AsyncIterator[None]:
        level = await _resolve_level(
            request, bot_id=bot_id, caller_id=caller_id, owner_id=owner_id
        )
        if level < rule.level:
            # The ids go to the log because the response cannot carry them: it
            # is a fixed "Not found", so this line is the only record of who
            # was refused what. All of them go through ``for_log``. This branch
            # runs only for values the server refused, so by construction they
            # are caller-chosen:
            # ``owner_id`` is a query parameter declared ``min_length=1`` with
            # no upper bound, and ``bot_id`` is a path segment that arrives
            # percent-decoded — so either can carry newlines or arbitrary bulk
            # and forge extra lines in the audit trail of refusals. ``caller_id``
            # is already bounded by matching the verified principal, but goes
            # through the same helper so the line has one rule, not two.
            logger.warning(
                "[bot_access] caller %s is below %s on bot=%s owner=%s",
                for_log(caller_id),
                rule.level.name,
                for_log(bot_id),
                for_log(owner_id),
            )
            # ``for_log`` in the exception message too. It never reaches a
            # caller — ``ENVELOPE_ERRORS`` maps this to a fixed "Not found" —
            # but ``app.py``'s 404 handler renders ``str(exc)`` through ``%s``,
            # so an unescaped id here would leak into that line by the back
            # door. Bounding it at the source keeps that handler identical to
            # its three siblings rather than making this one a special case.
            raise BotAccessRefusedError(f"bot {for_log(bot_id)} not found")

        if rule.edit_lock is not None:
            await _require_edit_lock(
                request,
                bot_id=bot_id,
                owner_id=owner_id,
                caller_id=caller_id,
            )

        # The ``yield`` is bare, and that is load-bearing rather than terse. An
        # exception escaping the handler is thrown back in here, and a bare
        # ``yield`` propagates it immediately, so everything below is skipped —
        # which is the only thing stopping an audit row from being written for
        # a mutation that never produced a response and therefore never
        # published a status for ``_succeeded`` to read. Wrapping this in
        # ``try``/``finally`` to "make sure the audit always runs" reintroduces
        # exactly that bug; ``test_a_mutation_that_never_produced_a_response_
        # writes_no_audit_row`` fails if anyone does.
        yield

        if _succeeded(request) and _is_audited(request) and level < PermissionLevel.OWNER:
            await _audit(
                request,
                bot_id=bot_id,
                owner_id=owner_id,
                actor_id=caller_id,
                route=_route_of(request),
            )

    return gate


def require_scaffold_edit_lock(rule: object) -> Callable[..., AsyncIterator[None]]:
    """Add the lock without replacing an owner/service authorization mode."""

    async def gate(
        request: Request,
        caller_id: UserIdDep,
    ) -> AsyncIterator[None]:
        raw_bot_id = request.path_params.get("bot_id")
        if raw_bot_id is None:
            raw_bot_id = request.path_params.get("bot_uuid")
        if not isinstance(raw_bot_id, str) or not raw_bot_id:
            raise BotEditLockCheckError("edit-lock target is unavailable")

        # BCS addresses a Bot as ``bot_id:entity_id``; the lock table uses the
        # backend Bot id only.
        bot_id = raw_bot_id.split(":", 1)[0]
        owner_id = caller_id
        if isinstance(rule, ServiceChecked):
            owner_id = await _owner_of(request, bot_id=bot_id)

        await _require_edit_lock(
            request,
            bot_id=bot_id,
            owner_id=owner_id,
            caller_id=caller_id,
        )
        yield

    return gate


async def _owner_of(request: Request, *, bot_id: str) -> str:
    """Resolve the owner for a service-checked route's authorized Bot."""
    bots = _service(request, BotRepository)
    if bots is None:
        raise BotEditLockCheckError("edit-lock target is unavailable")
    try:
        bot = await asyncio.to_thread(bots.get_by_id, bot_id)
    except Exception as exc:
        logger.exception(
            "[bot_access] edit-lock owner lookup failed for bot=%s; refusing",
            for_log(bot_id),
        )
        raise BotEditLockCheckError("edit-lock target is unavailable") from exc
    owner_id = (
        bot.get("owner_id")
        if isinstance(bot, dict)
        else getattr(bot, "owner_id", None)
    )
    if not isinstance(owner_id, str) or not owner_id:
        raise BotEditLockCheckError("edit-lock target is unavailable")
    return owner_id


async def _require_edit_lock(
    request: Request, *, bot_id: str, owner_id: str, caller_id: str
) -> None:
    """Require the Bot lock for collaborative service-Bot mutations."""
    locks = _service(request, CollaboratorLockServiceProtocol)
    if locks is None:
        logger.error(
            "[bot_access] edit-lock service is not wired for bot=%s; refusing",
            for_log(bot_id),
        )
        raise BotEditLockCheckError("edit-lock service is unavailable")

    if await _uses_member_management_semantics(
        request, bot_id=bot_id, owner_id=owner_id
    ):
        return

    try:
        info = await asyncio.to_thread(
            locks.get_lock_info,
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=caller_id,
        )
    except Exception as exc:
        logger.exception(
            "[bot_access] edit-lock lookup failed for bot=%s; refusing",
            for_log(bot_id),
        )
        raise BotEditLockCheckError("edit-lock state is unavailable") from exc

    if not info.has_collaborators:
        return
    if info.lock is None or info.lock.holder_user_id != caller_id:
        raise BotEditLockRequiredError("edit lock is not held by the caller")


async def _uses_member_management_semantics(
    request: Request, *, bot_id: str, owner_id: str
) -> bool:
    """Preserve the legacy session-lock exception for coding applications."""
    bots = _service(request, BotRepository)
    capabilities = _service(request, MemberManagementCapabilityProtocol)
    if bots is None or capabilities is None:
        return False
    try:
        bot = await asyncio.to_thread(bots.get_by_id_and_owner, bot_id, owner_id)
        return bool(bot and capabilities.uses_member_management_semantics(bot, bot_id))
    except Exception:
        logger.exception(
            "[bot_access] member-management capability lookup failed for bot=%s; "
            "using the service-Bot lock policy",
            for_log(bot_id),
        )
        return False


async def _resolve_level(
    request: Request, *, bot_id: str, caller_id: str, owner_id: str
) -> PermissionLevel:
    """:func:`_level`, with its database work off the event loop.

    The two lookups below are synchronous repository reads, and this runs inside
    an ``async`` dependency that FastAPI solves on the event-loop thread. Calling
    them inline parks that loop for the length of a database round trip and
    stalls every unrelated request the worker is serving — and unlike the audit
    write, this runs on **every** request to an adjudicated operation, not only
    on a non-owner mutation.

    Two places in this codebase already made the same call for the same reason:
    :func:`_audit` below (``asyncio.to_thread(log_repo.insert, ...)``) and
    ``EngineRuntimeRelay.resolve_bot_off_loop``, whose docstring spells out that
    an owner-scoped row read plus a collaborator query is "not cheap". The gate
    was written before it had a single adopter, so nothing ran here; adopting it
    across 82 routes — sessions and messages among them — is what put it on the
    hot path, and what makes this worth the thread.

    **Resolving the services stays on the loop.** Only the reads are offloaded,
    so nothing touches the injector off the event-loop thread — the same split
    :func:`_audit` documents. ``asyncio.to_thread`` copies the current
    ``contextvars.Context``, so the tenant and environment bindings the ORM
    guard reads survive the hop; the audit write has relied on that since #1323.
    """
    bots = _service(request, BotRepository)
    collaborators = _service(request, CollaboratorServiceProtocol)
    if bots is None or collaborators is None:
        logger.error(
            "[bot_access] cannot adjudicate bot=%s: the repository or the "
            "collaborator service is not wired; refusing",
            for_log(bot_id),
        )
        return PermissionLevel.NONE
    return await asyncio.to_thread(
        _level,
        bots,
        collaborators,
        bot_id=bot_id,
        caller_id=caller_id,
        owner_id=owner_id,
    )


def _level(
    bots: BotRepository,
    collaborators: CollaboratorServiceProtocol,
    *,
    bot_id: str,
    caller_id: str,
    owner_id: str,
) -> PermissionLevel:
    """The caller's level on the addressed bot, or ``NONE`` if anything failed.

    ``NONE`` is the answer to every question this cannot resolve — the bot does
    not exist under that owner, the repository is unavailable, the collaborator
    table cannot be read. Fail-closed is the whole contract; see the module
    docstring for why the internal interceptor's opposite choice is not ported.

    Synchronous and blocking by design: :func:`_resolve_level` is what keeps it
    off the event loop, and it takes its services as arguments so that this
    function never reaches for the injector from a worker thread.
    """
    try:
        bot = bots.get_by_id_and_owner(bot_id, owner_id)
    except Exception:
        logger.exception(
            "[bot_access] bot lookup failed for bot=%s; refusing", for_log(bot_id)
        )
        return PermissionLevel.NONE
    if not bot:
        return PermissionLevel.NONE
    try:
        return resolve_operable_permission_level(
            collaborators,
            bot=bot,
            user_id=caller_id,
            owner_id=owner_id,
        )
    except Exception:
        logger.exception(
            "[bot_access] collaborator lookup failed for bot=%s; refusing",
            for_log(bot_id),
        )
        return PermissionLevel.NONE


def _is_audited(request: Request) -> bool:
    """Whether this operation's success leaves a record. Reads do not."""
    return request.method.upper() not in _READ_METHODS


def _succeeded(request: Request) -> bool:
    """Whether the response actually reported success.

    Necessary because reaching this point does **not** mean the operation
    worked. ``@envelope_errors`` catches a mapped domain error and *returns* an
    error response rather than raising, so the handler completes normally and
    this teardown runs exactly as it would after a success. Auditing on arrival
    here would record mutations that never happened, which is worse than
    recording none: an incident review cannot tell a real action from a failed
    one.

    The status comes from ``access_log``, which publishes the wire status on
    the scope. A ``yield`` teardown runs *after* the response is sent, so that
    is the only vantage point where the outcome exists — the dependency itself
    never sees it, and neither does any middleware that would run earlier.

    **Unknown counts as success**, deliberately. An app without the public
    access-log middleware — a focused test app, say — records nothing, and the
    choice there is between dropping real audit rows and keeping a few
    doubtful ones. Only a *positively observed* failure suppresses the record,
    so this can never silently empty the trail.
    """
    status = getattr(request.state, RESPONSE_STATUS_KEY, None)
    return not isinstance(status, int) or status < 400


async def _audit(
    request: Request, *, bot_id: str, owner_id: str, actor_id: str, route: str
) -> None:
    """Record one non-owner action, and never fail the request for it.

    The action already happened when this runs — the handler has mutated state
    and the response is about to say so. Failing here would report an error for
    something that really succeeded, and a client retrying it would apply the
    mutation twice; a missing row is the smaller harm (``spec.md`` *Decisions*
    2). That is not the fail-closed check inverted: fail-closed prevents an
    action that has not happened yet, and after the fact there is nothing left
    to prevent.

    The cost is a silently incomplete trail, so the failure is loud here. If
    completeness ever becomes a hard requirement the answer is a durable
    outbox, not a synchronous write that can fail a request.

    **This is a considered exception to a binding repo rule, not an oversight.**
    ``AGENTS.md`` says: *"Propagate database and persistence write failures as
    errors; never silently swallow failed writes and return success."* That rule
    is right for a write the caller is asking for — its failure means the thing
    they requested did not happen, and saying otherwise is a lie. This write is
    the opposite case: the thing the caller requested *did* happen, and the
    record of it is a side effect. Applying the rule literally here would make a
    failed audit turn a successful mutation into an error response, and a client
    retrying it would apply the mutation twice — trading an incomplete log for
    duplicated state changes, which is the worse of the two.

    So the rule's real requirement — never *silently* swallow — is met by the
    ``logger.exception`` below rather than by failing the request. Recorded here
    because a reviewer reasonably read the code against that rule and flagged it
    (#1366, round 3); the reconciliation lived only in the seam feature's
    ``spec.md`` *Decisions* 2, which is not where anyone reading this function
    would look.
    """
    try:
        log_repo = _service(request, BotCollabLogRepositoryProtocol)
        if log_repo is None:
            raise RuntimeError("no BotCollabLogRepository bound")
        row = {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "operator_id": actor_id,
            "detail": json.dumps({"route": route, "method": request.method.upper()}),
        }
        # The repository is synchronous, and this runs inside an async teardown,
        # so calling it directly would block the worker's event loop on a
        # database write and stall every other request that worker is serving.
        # ``CollaboratorPermissionInterceptor.after`` offloads the same write
        # for the same reason, and this package already does it in five places
        # (``repository_catalog``, ``engine_runtime/connection``).
        #
        # Awaited rather than fire-and-forget, unlike the interceptor's
        # ``create_task``: the teardown runs *after* the response is sent, so
        # waiting costs the caller nothing, and it keeps the ``except`` below
        # able to see the failure. A detached task would drop that, which is
        # the one thing this function must not do quietly. Only the write goes
        # to the thread — resolving the repository stays on the loop, so
        # nothing touches the injector off it.
        await asyncio.to_thread(log_repo.insert, row)
    except Exception:
        # ``for_log`` here too, so the module has one rule for rendering a
        # caller-supplied id rather than one per branch. These three reached a
        # bot that resolved, so they are better bounded than the refusal
        # branch's — but this line is the *only* evidence that an audit row was
        # dropped, which is precisely the line worth keeping unforgeable.
        # ``route`` is the matched route template, which the server owns.
        logger.exception(
            "[bot_access] audit write failed and was dropped: bot=%s owner=%s "
            "actor=%s route=%s — the request succeeded and is not affected",
            for_log(bot_id),
            for_log(owner_id),
            for_log(actor_id),
            route,
        )


def _route_of(request: Request) -> str:
    """The matched route template, which is what makes audit rows aggregatable."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def _service(request: Request, protocol: type) -> Any | None:
    """Resolve a service off the app's injector, or ``None`` when unavailable.

    Pulled from ``request.app.state.injector`` rather than declared as an
    injected parameter, matching ``principal.py``'s ``_grant_reader``: this
    dependency is built at decoration time, before any injector exists, so it
    cannot receive dependencies by construction. ``None`` is never admitted as
    "no check needed" — every caller of this treats it as a refusal or a
    dropped audit row.
    """
    injector = getattr(request.app.state, "injector", None)
    if injector is None:
        return None
    try:
        return injector.get(protocol)
    except Exception:  # noqa: BLE001 - any resolution failure is "not wired"
        return None


__all__ = ["require_check", "require_scaffold_edit_lock"]
