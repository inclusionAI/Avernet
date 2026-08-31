"""Which public operations admit a caller with no human on the wire.

One table, and it is policy rather than plumbing: every operation on this
surface appears in :data:`ADMISSION` exactly once, and an operation's mode
follows from its **shape** — which identities it takes, and how it resolves the
bot it acts on — not from taste. ``test_principal_seam.py`` fails if the surface
and this table disagree in either direction, so a route added tomorrow is
refused until someone puts it in a group on purpose.

The table itself lives in ``admission_table`` — this module reached the
1000-line cap, and a per-route table is the half that grows with the surface
while the seam below does not. It is re-exported here, so ``from …admission
import ADMISSION`` is unchanged, and the rules it follows are restated where it
now lives.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.adapters.http.openapi_v1.admission_modes import (
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    GrantNotResolvableError,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.adapters.http.openapi_v1.admission_table import (
    ADMISSION,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Kept as an explicit empty set so the admission-inventory test continues to
#: make any future handler-level grant exception visible in review.
SKILL_SCOPED_OPERATIONS = frozenset()

#: Harness operations resolve the bot owner from the repository record rather
#: than from an ``owner_id`` query parameter. This preserves the existing wire
#: contract (``bot_id`` on the path, ``user_id`` in the query) while still
#: running an addressed-bot grant check for application callers inside the
#: handler's own ``require_harness_bot_access`` dependency.
HARNESS_SCOPED_OPERATIONS = frozenset({
    ("POST", "/openapi/v1/bots/{bot_id}/harness/diagnose"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/preview"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/apply"),
    ("POST", "/openapi/v1/bots/{bot_id}/harness/rollback"),
    ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-report"),
    ("GET", "/openapi/v1/bots/{bot_id}/harness/dim-history"),
})

#: The modes that admit a caller naming no end user. Everything else refuses at
#: ``require_principal``, which is what a route inherits by saying nothing.
ADMITTING_MODES = frozenset(
    {
        AdmissionMode.GRANT_CHECKED_OWN_BOT,
        AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT,
        AdmissionMode.GRANT_FILTERED,
        AdmissionMode.USER_GATED,
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

    def require_bot(self, bot_id: str, *, owner_id: str) -> str:
        """Confirm the application may act on the addressed bot; return its owner.

        **It returns the owner, never the delegating user**, and the two are the
        same person only when someone addresses their own bot. The value is
        ``owner_id`` — the bot this request addresses — handed back once it is
        known to be covered:

        - a **human** caller is not governed by a grant, so there is nothing to
          check and the addressed owner passes straight through.
        - an **application** must hold a live grant for
          ``(app, bot, owner, delegating user)``. The grant's own ``owner_id``
          equals the one passed in, because that is what it was looked up by —
          so the returned value is the same either way, and the difference is
          only whether it was allowed to be returned at all.

        The engine-runtime groups consume it as the owner of the bot they are
        about to operate; the user-scoped groups discard it, having addressed
        their own bot by construction.

        A missing grant raises :class:`GrantNotResolvableError`, which the app
        maps to a ``404`` byte-identical to a nonexistent bot: an application
        must not be able to tell a bot it was not granted from one that does not
        exist.

        **The pair is the address, and the lookup takes the pair.** ``ac_bots``
        has no unique key on ``bot_id`` — the legacy ``default`` convention gave
        many owners one — so a probe keyed on the id alone asks a question with
        more than one answer. An earlier revision did exactly that and compared
        ``record.owner_id`` afterwards: safe while the unique key happened to
        make the row singular, and it foreclosed ever keying the record on the
        bot's real identity, because the read could not have supplied it.

        A grant is not the whole answer. It says the delegation exists; whether
        the delegating user may still operate the bot is asked separately, live,
        by the same gate they would face — which is what stops a delegation
        outliving the access it lends.
        """
        if self.app_id is None:
            return owner_id
        if self.grants is None:
            # Unreachable through the seam, which always supplies the reader for
            # an application. Refusing rather than defaulting, because the
            # alternative to a grant check here is no check at all.
            raise GrantNotResolvableError("no grant reader for an application caller")
        record = self.grants.find(
            bot_id=bot_id,
            owner_id=owner_id,
            user_id=self.user_id,
            app_id=self.app_id,
        )
        if record is None:
            # Which user, which owner and which bot were asked for go to the
            # **log**, not into the exception message. The message is carried
            # into a log line verbatim by the handlers in ``app.py`` and
            # ``error_logging.py``, and all three are caller-chosen and
            # unbounded — so interpolating them there would let the party being
            # refused inject extra log lines and choose how many bytes each
            # refusal costs.
            #
            # ``app_id`` is safe to name: it is an int off the verified
            # principal, not something the request supplied.
            logger.warning(
                "[bot_app_grant] app_id=%s holds no live grant from user=%s "
                "on bot=%s owned by=%s",
                self.app_id,
                for_log(self.user_id),
                for_log(bot_id),
                for_log(owner_id),
            )
            raise GrantNotResolvableError(
                f"app {self.app_id} holds no live grant for the requested bot"
            )
        return record.owner_id

    def granted_bot_ids(
        self, *, owned_by_delegator: bool = False
    ) -> frozenset[str] | None:
        """The bots to narrow a listing to, or ``None`` to not narrow at all.

        ``None`` and the empty set are different answers and must stay so:
        ``None`` is a human caller, whose listing is not filtered; an empty set
        is an application that has been granted nothing, whose listing is empty.
        Collapsing them would hand an ungranted application the delegating
        user's entire bot list.

        ``owned_by_delegator`` narrows further, to grants naming the delegating
        user as the bot's owner, and an **owner-scoped listing must set it**.
        The ids are bare ``bot_id`` strings, and ``bot_id`` is not unique across
        owners: filtering an owner-scoped query by a set that includes someone
        else's ``default`` matches the delegating user's own ``default`` and
        returns a bot nobody granted. Nothing is lost by narrowing — an
        owner-scoped listing cannot show a bot the user does not own anyway.

        Callers that only ask *whether any delegation exists* leave it off:
        there the question is about the relationship, not about which bots.
        """
        if self.app_id is None:
            return None
        if self.grants is None:
            return frozenset()
        records = self.grants.list_for_app(app_id=self.app_id, user_id=self.user_id)
        return frozenset(
            record.bot_id
            for record in records
            if not owned_by_delegator or record.owner_id == self.user_id
        )


__all__ = [
    "ADMISSION",
    "ADMITTING_MODES",
    "HARNESS_SCOPED_OPERATIONS",
    "SKILL_SCOPED_OPERATIONS",
    "ActingCaller",
    "AdmissionMode",
]
