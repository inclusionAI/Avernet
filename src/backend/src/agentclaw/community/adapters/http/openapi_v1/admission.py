"""Which public operations admit a caller with no human on the wire.

One table, and it is policy rather than plumbing: every operation on this
surface appears in :data:`ADMISSION` exactly once, and an operation's mode
follows from its **shape** — which identities it takes, and how it resolves the
bot it acts on — not from taste. ``test_principal_seam.py`` fails if the surface
and this table disagree in either direction, so a route added tomorrow is
refused until someone puts it in a group on purpose.

Two id models, and both spell their parameter ``user_id``
--------------------------------------------------------

Missing this is the mistake this module exists to prevent.

**User-scoped groups** (``bots``, ``resources``, ``routines``, ``skills``,
``identity``, ``mcp``) resolve the bot with ``get_by_id_and_owner(bot_id,
user_id)``. Caller and owner are necessarily the same person; a non-owner gets a
masked ``404``. A bot merely *shared* with the caller is unreachable here **for
a human too**, so an application acting as that human inherits the same limit
without anything being written to enforce it.

**Engine-runtime groups** (``sessions``, ``engine``, ``models``, ``approvals``,
``connection``) take ``user_id`` as the *caller* and a second ``owner_id``
naming the *addressed bot's owner*, then adjudicate through the collaborator
gate. This is where a shared bot is reachable, and therefore where a delegation
actually pays off. For an app-only caller the addressed owner comes from the
**grant record**, never from the request — see ``engine_runtime/params.py``.

The invariant every mode below serves
-------------------------------------

    An application's reach is exactly its granting user's reach, and never more.

Not a copy taken at consent time — the live thing. The grant says only "this
application may act as this person"; whether that person may still operate that
bot is asked again on every request, by the same gate they would face
themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.adapters.http.openapi_v1.errors import (
    GrantNotResolvableError,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.log import get_logger

logger = get_logger()


class AdmissionMode(StrEnum):
    """How an operation treats a caller that names no end user."""

    #: Names a bot; caller must *be* its owner. Admitted iff a live grant covers
    #: ``(app, bot, delegating user)``.
    A1 = "grant-checked"
    #: Names a bot *and* an owner. A1's check, plus the addressed owner taken
    #: from the grant record rather than the request.
    A2 = "grant-checked-owner-addressed"
    #: Returns a set of bots. Admitted; the result is narrowed to granted ones.
    B = "grant-filtered"
    #: No bot dimension, but concerns the named user's account. Admitted only
    #: while the application holds at least one live grant from that user.
    C = "user-gated"
    #: No bot dimension and no ``user_id``; the answer is identical for every
    #: authenticated caller in the tenant. Admitted on authentication alone.
    OPEN = "open"
    #: Refused. The default, and what an unlisted route effectively gets.
    REFUSED = "refused"


#: Every public operation, keyed by ``(method, path)`` exactly as FastAPI
#: reports it. Grouped by mode, with the reason each group has the mode it has.
#:
#: **This table has a counterpart at the edge.** The gateway's
#: ``route_security`` (``src/gateway/configs/application.yaml``) decides which
#: identities are *resolvable* for a path; this decides which operations admit a
#: machine caller once they arrive. Both must agree that a ``REFUSED`` operation
#: still requires a human — an operation left open at both hops because someone
#: edited only one is the hole the pair exists to prevent.
#:
#: The agreement is pinned on the gateway side
#: (``tests/unit/core/authn/test_route_security.py``), because that is where the
#: path matcher lives and a second implementation of "most specific" is exactly
#: what ``gateway/core/paths/_pattern.py`` exists to prevent. Change ``REFUSED``
#: here and that test is the one that will fail.
ADMISSION: dict[tuple[str, str], AdmissionMode] = {
    # ── A1: names a bot, resolved owner-scoped ───────────────────────────────
    # The caller can only ever reach their own bots here, so an application
    # acting as them can only reach the same ones.
    ("GET", "/openapi/v1/bots/{bot_id}"): AdmissionMode.A1,
    ("PUT", "/openapi/v1/bots/{bot_id}"): AdmissionMode.A1,
    ("DELETE", "/openapi/v1/bots/{bot_id}"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/{bot_id}/restart"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/{bot_id}/auth-status"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/{bot_id}/status"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/{bot_id}/passport"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/{bot_id}/engine-config"): AdmissionMode.A1,
    ("PUT", "/openapi/v1/bots/{bot_id}/engine-config"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/identity/{bot_id}"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): AdmissionMode.A1,
    ("PUT", "/openapi/v1/bots/identity/{bot_id}/{file_type}"): AdmissionMode.A1,
    # resources — ``bot_id`` is a required query parameter on all nine.
    ("GET", "/openapi/v1/bots/resources"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/resources"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/resources/check-name"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/resources/upload"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/resources/{resource_id}"): AdmissionMode.A1,
    ("PUT", "/openapi/v1/bots/resources/{resource_id}"): AdmissionMode.A1,
    ("DELETE", "/openapi/v1/bots/resources/{resource_id}"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/resources/{resource_id}/download"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/resources/{resource_id}/preview"): AdmissionMode.A1,
    # routines — query ``bot_id``, except the create, which carries it in the
    # body and is checked in the handler (see ``BODY_BOT_ID_OPERATIONS``).
    ("GET", "/openapi/v1/bots/routines"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/routines"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/routines/{routine_id}"): AdmissionMode.A1,
    ("PATCH", "/openapi/v1/bots/routines/{routine_id}"): AdmissionMode.A1,
    ("DELETE", "/openapi/v1/bots/routines/{routine_id}"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/routines/{routine_id}/run"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/routines/{routine_id}/runs"): AdmissionMode.A1,
    # skills — the first two carry ``bot_id``; the four ``{skill_id}`` routes
    # carry none and must resolve it from the skill (``SKILL_SCOPED_OPERATIONS``).
    ("GET", "/openapi/v1/bots/skills"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/skills/upload"): AdmissionMode.A1,
    ("GET", "/openapi/v1/bots/skills/{skill_id}"): AdmissionMode.A1,
    ("DELETE", "/openapi/v1/bots/skills/{skill_id}"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/activate"): AdmissionMode.A1,
    ("POST", "/openapi/v1/bots/skills/{skill_id}/deactivate"): AdmissionMode.A1,
    # ── A2: names a bot *and* an owner, adjudicated by the collaborator gate ──
    ("GET", "/openapi/v1/bots/sessions/{bot_id}"): AdmissionMode.A2,
    ("POST", "/openapi/v1/bots/sessions/{bot_id}"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): AdmissionMode.A2,
    ("PATCH", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): AdmissionMode.A2,
    ("DELETE", "/openapi/v1/bots/sessions/{bot_id}/{session_id}"): AdmissionMode.A2,
    (
        "GET",
        "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages",
    ): AdmissionMode.A2,
    (
        "DELETE",
        "/openapi/v1/bots/sessions/{bot_id}/{session_id}/messages",
    ): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/available"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/capabilities"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/engine/{bot_id}/status"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/mode"): AdmissionMode.A2,
    ("PUT", "/openapi/v1/bots/approvals/{bot_id}/mode"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/approvals/{bot_id}/modes"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/models/{bot_id}"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/models/{bot_id}/{model_id:path}"): AdmissionMode.A2,
    ("GET", "/openapi/v1/bots/connection/{bot_id}"): AdmissionMode.A2,
    # ── B: returns a set of bots, narrowed to the granted ones ───────────────
    ("GET", "/openapi/v1/bots"): AdmissionMode.B,
    # The application's own view, and the **complete** one: a granted bot the
    # delegating user does not own appears in no listing of that user's bots, so
    # without this it would be undiscoverable.
    ("GET", "/openapi/v1/bots/authorized"): AdmissionMode.B,
    # ── C: no bot dimension, but about the named user's account ──────────────
    ("GET", "/openapi/v1/bots/ceiling"): AdmissionMode.C,
    # ── OPEN: no user on the wire, tenant-identical answer ───────────────────
    # Not a new exposure: every authenticated caller in the tenant already gets
    # the identical answer, and there is no user here to gate against.
    ("GET", "/openapi/v1/bots/check-name"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/mcp/servers"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/mcp/servers/{server_code}"): AdmissionMode.OPEN,
    ("GET", "/openapi/v1/bots/mcp/tenants"): AdmissionMode.OPEN,
    # ── REFUSED — each for its own reason ────────────────────────────────────
    # No bot exists yet for a grant to cover, and creation spends the user's
    # quota. Auto-granting the new bot would invent consent nobody gave.
    ("POST", "/openapi/v1/bots"): AdmissionMode.REFUSED,
    # Delegation is a human act. An application must not be able to widen its
    # own access, withdraw a competitor's, or enumerate what else reaches a bot.
    ("POST", "/openapi/v1/bots/{bot_id}/authorized-apps"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/{bot_id}/authorized-apps"): AdmissionMode.REFUSED,
    (
        "DELETE",
        "/openapi/v1/bots/{bot_id}/authorized-apps/{app_id}",
    ): AdmissionMode.REFUSED,
    # Bot logs: here ``user_id`` means *whose traces to read* over a
    # tenant-level observability surface, not *whose call this is*. A grant
    # covers a bot; it does not translate into that meaning.
    ("GET", "/openapi/v1/bots/logs/traces"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/logs/traces/{trace_id}"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/logs/sessions/{session_key}/traces"): AdmissionMode.REFUSED,
    ("GET", "/openapi/v1/bots/logs/groups/{group_id}/traces"): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces",
    ): AdmissionMode.REFUSED,
    # MCP *configuration* — account-level state with no bot dimension. A grant
    # is consent to reach a bot, not to reconfigure an account. (The catalogue
    # reads above are a different thing and are OPEN.)
    ("GET", "/openapi/v1/bots/mcp/servers/{server_code}/config"): AdmissionMode.REFUSED,
    ("PUT", "/openapi/v1/bots/mcp/servers/{server_code}/config"): AdmissionMode.REFUSED,
    (
        "GET",
        "/openapi/v1/bots/mcp/servers/{server_code}/permissions",
    ): AdmissionMode.REFUSED,
    # Load-test endpoints: no user scope, no bot, and nothing this feature is
    # about. Left exactly as they were.
    ("GET", "/openapi/v1/bots/loadtest/hello"): AdmissionMode.REFUSED,
    ("WEBSOCKET", "/openapi/v1/bots/loadtest/ws/echo"): AdmissionMode.REFUSED,
}

#: A1 operations whose ``bot_id`` is in the request **body**, so the grant can
#: only be checked once the body is parsed — inside the handler, immediately,
#: before any service call.
BODY_BOT_ID_OPERATIONS = frozenset({("POST", "/openapi/v1/bots/routines")})

#: A1 operations that name a *skill* and no bot. The bot is resolved from the
#: skill through the existing user-scoped read — so another user's skill is
#: refused before the grant is even consulted — and the grant checked against
#: the result.
SKILL_SCOPED_OPERATIONS = frozenset(
    {
        ("GET", "/openapi/v1/bots/skills/{skill_id}"),
        ("DELETE", "/openapi/v1/bots/skills/{skill_id}"),
        ("POST", "/openapi/v1/bots/skills/{skill_id}/activate"),
        ("POST", "/openapi/v1/bots/skills/{skill_id}/deactivate"),
    }
)

#: The modes that admit a caller naming no end user. Everything else refuses at
#: ``require_principal``, which is what a route inherits by saying nothing.
ADMITTING_MODES = frozenset(
    {
        AdmissionMode.A1,
        AdmissionMode.A2,
        AdmissionMode.B,
        AdmissionMode.C,
        AdmissionMode.OPEN,
    }
)


@dataclass(frozen=True)
class ActingCaller:
    """Who a request acts for, and what the calling application may reach.

    Built once per request by the seam in ``principal.py``. It carries the two
    ids the surface scopes by, and the one question only a grant can answer.
    """

    #: The end user this request acts for. For a human caller, themselves; for
    #: an application, the user who delegated to it. Downstream code cannot tell
    #: the difference, which is the point — an admitted application is that user
    #: for the length of the request, bounded by their live access.
    user_id: str

    #: The calling application, or ``None`` for a human caller.
    #:
    #: ``None`` is a real state of the contract — "no grant applies to this
    #: caller" — rather than a widened type, and every consumer branches on it
    #: explicitly. That is what keeps a human from being resolved against a
    #: grant, and an application from falling through to an unscoped read.
    app_id: int | None

    #: The grant reader. Never consulted for a human caller.
    grants: BotAppGrantServiceProtocol | None = None

    @property
    def is_application(self) -> bool:
        """Whether a grant governs this request."""
        return self.app_id is not None

    def require_bot(self, bot_id: str) -> str:
        """The addressed bot's owner, refusing an application without a grant.

        For a **human** caller this returns their own id and asks nothing: on
        the user-scoped groups the owner *is* the caller, and on the
        engine-runtime groups the request's own ``owner_id`` parameter overrides
        this anyway.

        For an **application** it is the authorization probe — a unique-key
        lookup on ``(app, bot, delegating user)``. A missing grant raises
        :class:`GrantNotResolvableError`, which the app maps to a ``404``
        byte-identical to a nonexistent bot: an application must not be able to
        tell a bot it was not granted from one that does not exist.

        What comes back is the **grant's** owner, not the request's. That is
        what lets an application address a bot its delegating user collaborates
        on without ever being able to name an owner of its own choosing.

        A grant is not the whole answer. It says the delegation exists; whether
        the delegating user may still operate the bot is asked separately, live,
        by the same gate they would face — which is what stops a delegation
        outliving the access it lends.
        """
        if self.app_id is None:
            return self.user_id
        if self.grants is None:
            # Unreachable through the seam, which always supplies the reader for
            # an application. Refusing rather than defaulting, because the
            # alternative to a grant check here is no check at all.
            raise GrantNotResolvableError("no grant reader for an application caller")
        record = self.grants.find(bot_id=bot_id, user_id=self.user_id, app_id=self.app_id)
        if record is None:
            # Which user and which bot were asked for goes to the **log**, not
            # into the exception message. The message is carried into a log line
            # verbatim by the handlers in ``app.py`` and ``error_logging.py``,
            # and both of these values are caller-chosen and unbounded — so
            # interpolating them there would let the party being refused inject
            # extra log lines and choose how many bytes each refusal costs.
            #
            # ``app_id`` is safe to name: it is an int off the verified
            # principal, not something the request supplied.
            logger.warning(
                "[bot_app_grant] app_id=%s holds no live grant from user=%s "
                "on bot=%s",
                self.app_id,
                for_log(self.user_id),
                for_log(bot_id),
            )
            raise GrantNotResolvableError(
                f"app {self.app_id} holds no live grant for the requested bot"
            )
        return record.owner_id

    def granted_bot_ids(self) -> frozenset[str] | None:
        """The bots to narrow a listing to, or ``None`` to not narrow at all.

        ``None`` and the empty set are different answers and must stay so:
        ``None`` is a human caller, whose listing is not filtered; an empty set
        is an application that has been granted nothing, whose listing is empty.
        Collapsing them would hand an ungranted application the delegating
        user's entire bot list.
        """
        if self.app_id is None:
            return None
        if self.grants is None:
            return frozenset()
        records = self.grants.list_for_app(app_id=self.app_id, user_id=self.user_id)
        return frozenset(record.bot_id for record in records)


__all__ = [
    "ADMISSION",
    "ADMITTING_MODES",
    "BODY_BOT_ID_OPERATIONS",
    "SKILL_SCOPED_OPERATIONS",
    "ActingCaller",
    "AdmissionMode",
]
