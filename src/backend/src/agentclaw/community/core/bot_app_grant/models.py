"""Persistence models for owner-granted bot authorizations.

Two tables, and the split is the design rather than a filing decision.

An authorization has to answer two questions that pull against each other:
*may this app reach this bot right now* — one answer, which wants a unique key —
and *when could it, historically* — unboundedly many answers, which wants no key
at all. One table cannot serve both. Soft-deleting a single row looks like it
bridges them and does not: putting a status column in the unique key survives
grant → withdraw → grant and then fails on the **second** withdrawal, when two
revoked rows collide on the remaining columns. The textbook fix is a filtered
unique index (``WHERE status='active'``), and MySQL — therefore OceanBase — has
none.

So each question gets the table it needs. :class:`BotAppGrantModel` holds live
grants only: a row exists if and only if the app may reach the bot, which is why
it needs neither a status column nor a revocation timestamp, and why its unique
key is a real constraint the database enforces. :class:`BotAppGrantLogModel` is
append-only with no unique key whatsoever — its job is to accept every event,
including the fourth revocation of the same pair.

This is the shape ``ac_bot_collaborator`` / ``ac_bot_collab_log`` already uses.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.plugin_api.models import AutoIncrementBigInteger
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard
from agentclaw.community.utils.env_utils import get_current_env


#: How much of an application's display name a grant row keeps.
#:
#: The gateway's ``app_name`` is unconstrained at its own boundary, so *any*
#: finite column here has an input that exceeds it — moving the number is not a
#: fix, it only relocates the cliff. The value is therefore truncated
#: deliberately at write time rather than left to the database, which would
#: either reject the grant (strict mode) or truncate it silently (permissive).
#:
#: Truncating is safe **because this column is not identity**. ``app_id`` is what
#: the record is keyed and resolved by; ``app_name`` exists so a human scanning a
#: listing recognises the application, and 1024 characters is past the point of
#: recognition. An authorization must not fail because a display name is long.
APP_NAME_MAX_LENGTH = 1024


class GrantAction(StrEnum):
    """What a log row records. The live table needs no such enum — existence
    is its only state — so this belongs to the history alone."""

    GRANTED = "granted"
    REVOKED = "revoked"


class BotAppGrantRecord(BaseModel):
    """A live authorization, as the service and adapter see it.

    Carries ``avernet_tenant`` even though no HTTP response exposes it: it is
    the anchor the later machine-caller path resolves ownership against, and
    that path must read it off the record rather than off the wire.
    """

    id: int = Field(..., description="Primary key")
    app_id: int = Field(..., description="Gateway avernet_application.id")
    app_name: str = Field(..., description="App display name as at consent time")
    bot_id: str = Field(..., description="The authorized bot")
    user_id: str = Field(..., description="The delegating user, resolved server-side")
    owner_id: str = Field(..., description="Bot owner, resolved server-side")
    avernet_tenant: str = Field(..., description="Data-isolation tenant")
    env: str = Field(..., description="Environment marker")
    gmt_create: datetime = Field(..., description="When this authorization began")


class BotAppGrantModel(Base):
    """Live grants only — one row iff the app may reach the bot right now.

    A row means **"app A may act as user U on bot B, which O owns"**. The two
    user columns are two different people whenever the bot is shared, and
    collapsing them was the first design mistake this table made:

    - ``user_id`` is the **delegating user** — whose access is being lent. It is
      the key column, because the delegation is theirs.
    - ``owner_id`` is the bot's **owner**, denormalized so the machine-caller
      path can address the bot without a second lookup. It may name someone who
      has never heard of the application.

    ``app_id`` is the gateway's ``avernet_application.id``, not a token, so an
    authorization survives the app rotating its credential.

    **Nothing about the delegator's authority is stored here** — no permission
    level, no capability list — and that absence is the design. The bound on
    what the application may do is the delegator's *live* access, re-adjudicated
    on every request by the same collaborator gate the human faces. A snapshot
    taken at consent time would go stale in the dangerous direction: it would
    keep answering "yes" after the delegator was removed from the bot. Because
    nothing is copied, losing that access ends the application's access on the
    next request, with no revocation and nothing to clean up.
    """

    __tablename__ = "ac_bot_app_grant"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    app_id = Column(
        AutoIncrementBigInteger, nullable=False, comment="gateway avernet_application.id"
    )
    # 1024, matching the unconstrained gateway boundary (``AppRow.app_name``)
    # as closely as a VARCHAR can. Widening is free here precisely because
    # ``app_name`` is in no index — contrast ``owner_id`` below, which is in the
    # unique key and cannot grow without pushing it past InnoDB's 3072-byte cap.
    app_name = Column(
        String(1024), nullable=False, comment="app display name, snapshotted at consent"
    )
    bot_id = Column(String(256), nullable=False, comment="the authorized bot")
    # The delegating user. In the unique key, so it is held at 256 for the same
    # reason ``owner_id`` is — see the key's own note.
    #
    # The DDL pins ``COLLATE utf8mb4_bin`` on this column and the ORM does not,
    # which is not drift but the only portable option: the local runtime is
    # SQLite, which has no such collation and would fail ``create_all``. Every
    # comparison on this column is against a bound parameter rather than another
    # column, so the two runtimes agree regardless.
    user_id = Column(
        String(256), nullable=False, comment="delegating user, resolved server-side"
    )
    owner_id = Column(
        String(256), nullable=False, comment="bot owner, resolved server-side"
    )
    env = Column(String(20), nullable=False, default=get_current_env)
    # Data-isolation tenant (see utils/avernet_tenant_guard + the registration
    # below). server_default (not a Python default=) so create_all emits the
    # same DEFAULT 'teamclaw' prod's out-of-band DDL applies, backfilling
    # existing rows and covering any non-ORM insert; the context-aware value on
    # ORM inserts comes from the insert guard.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # ``avernet_tenant`` leads the key for the reason ``ac_user_mcp_config``
        # documents: ``owner_id`` is a user identifier, meaningful only *within*
        # a tenant, so two tenants may each hold a "12345" who owns a bot of the
        # same name. Without the tenant here the second tenant's grant would
        # fail against a row it cannot even see.
        #
        # ``env`` is in the key so one authorization cannot collide across
        # environments sharing a database, matching ``uk_bot_pk_user_env`` on
        # ``ac_bot_collaborator``.
        #
        # No ``bot_pk``: ``bot_id`` alone is not unique across owners, but
        # ``bot_id`` + a user id carries the same uniqueness without putting a
        # surrogate key into a record the public surface returns.
        #
        # ``user_id`` and **not** ``owner_id``, for two independent reasons.
        #
        # Semantics: uniqueness is per delegation, and a delegation belongs to
        # the person making it. Two collaborators may each authorize the same
        # app for the same bot; those are two separate loans of two separate
        # authorities, independently withdrawable. Keyed on ``owner_id`` they
        # would collide, and the idempotent grant path would silently swallow
        # the second as "already live" — handing the second user an application
        # bounded by the *first* user's access.
        #
        # Budget: the key is 2392 bytes (``avernet_tenant`` 64x4 + ``app_id`` 8
        # + ``bot_id`` 256x4 + one user id 256x4 + ``env`` 20x4). Carrying both
        # user columns would be 3416, past InnoDB's 3072-byte cap — the same
        # wall that holds these columns at 256 characters in the first place. So
        # this was never a choice between one and both.
        UniqueConstraint(
            "avernet_tenant",
            "app_id",
            "bot_id",
            "user_id",
            "env",
            name="uk_bot_app_grant_scope",
        ),
        # The app's view — "which bots may I reach as this user?". Not redundant
        # with the unique key above: that key reaches ``bot_id`` before
        # ``user_id``, so it cannot serve a lookup that names no bot.
        Index(
            "idx_bot_app_grant_app_user",
            "avernet_tenant",
            "app_id",
            "user_id",
            "env",
        ),
        # The bot's view — "which apps can reach this bot, and who let them in?".
        # Needs its own index for the same reason, mirrored: the unique key and
        # the index above both put ``app_id`` immediately after the tenant, and
        # a B-tree cannot reach the later columns without an ``app_id``
        # predicate. This listing supplies none, so without this it degrades
        # into a tenant-wide scan as grants accumulate.
        #
        # Its ``(avernet_tenant, bot_id)`` prefix now serves two reads that name
        # no delegating user either — the owner's cross-delegator listing and
        # the sweep that revokes every grant when a bot is deleted — which is
        # why ``owner_id`` staying in it costs nothing and it needs no rekeying.
        Index(
            "idx_bot_app_grant_bot_owner",
            "avernet_tenant",
            "bot_id",
            "owner_id",
            "env",
        ),
    )

    def to_record(self) -> BotAppGrantRecord:
        """Convert to the Pydantic record the service returns."""
        return BotAppGrantRecord(
            id=self.id,
            app_id=self.app_id,
            app_name=self.app_name,
            bot_id=self.bot_id,
            user_id=self.user_id,
            owner_id=self.owner_id,
            avernet_tenant=self.avernet_tenant,
            env=self.env,
            gmt_create=self.gmt_create,
        )


class BotAppGrantLogModel(Base):
    """Append-only history — one row per grant and per withdrawal, never updated.

    **No unique constraint, deliberately.** Accepting every event *is* the
    contract: a pair granted and withdrawn four times produces eight rows, and
    any key on the scope columns would reintroduce exactly the collision the
    two-table split exists to remove.

    ``app_name`` and ``avernet_tenant`` are duplicated here rather than joined
    from the live table, because the live row is gone by the time a revocation
    is audited — which is precisely when this table is read.
    """

    __tablename__ = "ac_bot_app_grant_log"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    app_id = Column(AutoIncrementBigInteger, nullable=False)
    app_name = Column(String(1024), nullable=False)
    bot_id = Column(String(256), nullable=False)
    # Free to add, unlike on the live table: this one has no unique key by
    # design, so nothing here is constrained by a byte budget. "Who let this
    # application in, and when" has to stay answerable after the live row is
    # gone — which is exactly when this table is read.
    user_id = Column(String(256), nullable=False)
    owner_id = Column(String(256), nullable=False)
    action = Column(
        String(32), nullable=False, comment=f"{GrantAction.GRANTED} | {GrantAction.REVOKED}"
    )
    env = Column(String(20), nullable=False, default=get_current_env)
    # See the note on BotAppGrantModel.avernet_tenant.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        # Reconstruct one bot's authorization history in order. ``gmt_create``
        # trails the scope columns so the range scan comes out sorted.
        Index(
            "idx_bot_app_grant_log_bot",
            "avernet_tenant",
            "bot_id",
            "owner_id",
            "env",
            "gmt_create",
        ),
    )


# Confine every read/update/delete to the request's tenant and stamp it on every
# insert. Both tables are registered, and the log's registration is not
# ceremonial: it is read *after* the live row is deleted, so it has no guarded
# parent left to inherit isolation from at the moment it matters most.
#
# The insert guard also supplies this feature's cross-tenant refusal outright —
# a grant naming a tenant other than the request's raises CrossTenantInsertError
# rather than being written — so no hand-rolled tenant comparison is needed.
register_avernet_tenant_guard(BotAppGrantModel)
register_avernet_tenant_guard(BotAppGrantLogModel)


__all__ = [
    "BotAppGrantLogModel",
    "BotAppGrantModel",
    "BotAppGrantRecord",
    "GrantAction",
]
