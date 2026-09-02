"""Persistence models for user-granted account-level authorizations.

Two tables, for the reason ``core/bot_app_grant/models.py`` gives at length: the
live table answers *may this app act as this user right now* — one answer, so
it carries a unique key the database enforces — and the log answers *when could
it, historically* — unboundedly many answers, so it carries no unique key at
all. MySQL, and therefore OceanBase, has no filtered unique index, so one
soft-deleted table cannot hold both.

A row here names **one person**, not two. The bot-level record carries a
delegating user and a bot owner because a bot may be shared; an account-level
authorization is about the user's own account, so ``user_id`` is both the
delegator and the subject.
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


#: How much of an application's display name a grant row keeps. Truncated at
#: write time rather than by the column, and safe to truncate because the name
#: is not identity — ``app_id`` is. Same reasoning as the bot-level record.
APP_NAME_MAX_LENGTH = 1024

#: How long a user id may be for a grant to be *representable*. The column is
#: in the unique key, so this is a real limit rather than a chosen one, and it
#: matches the bot-level record so one user id is storable in both or neither.
#:
#: Refused at consent time rather than truncated: ``user_id`` is the column
#: every app-only request resolves on, and a truncated value produces a row no
#: lookup can match — an authorization that looks live in every listing and
#: admits nothing.
IDENTITY_MAX_LENGTH = 256


class UserGrantAction(StrEnum):
    """What a log row records. The live table needs no such enum — existence
    is its only state — so this belongs to the history alone."""

    GRANTED = "granted"
    REVOKED = "revoked"


class UserAppGrantRecord(BaseModel):
    """A live account-level authorization, as the service and adapter see it."""

    id: int = Field(..., description="Primary key")
    app_id: int = Field(..., description="Gateway avernet_application.id")
    app_name: str = Field(..., description="App display name as at consent time")
    user_id: str = Field(..., description="The authorizing user, resolved server-side")
    avernet_tenant: str = Field(..., description="Data-isolation tenant")
    env: str = Field(..., description="Environment marker")
    gmt_create: datetime = Field(..., description="When this authorization began")


class UserAppGrantModel(Base):
    """Live grants only — one row iff the app may act as the user right now.

    A row means **"app A may act as user U at the account level"**. It admits
    the application to the operations ``admission.py`` marks ``USER_GATED``;
    it says nothing about any bot, which is the bot-level record's question.

    ``app_id`` is the gateway's ``avernet_application.id``, not a token, so an
    authorization survives the app rotating its credential.

    Nothing about the user's own authority is stored here. What the
    application may then do is bounded by that user's live access on every
    request — Space membership, work-order recipiency, device ownership — each
    enforced by the service that owns it, exactly as for the human.
    """

    __tablename__ = "ac_user_app_grant"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    app_id = Column(
        AutoIncrementBigInteger, nullable=False, comment="gateway avernet_application.id"
    )
    app_name = Column(
        String(1024), nullable=False, comment="app display name, snapshotted at consent"
    )
    # The DDL pins ``COLLATE utf8mb4_bin`` on this column and the ORM does not;
    # the local runtime is SQLite, which has no such collation. Every comparison
    # is against a bound parameter, so both runtimes agree.
    user_id = Column(
        String(256), nullable=False, comment="authorizing user, resolved server-side"
    )
    env = Column(String(20), nullable=False, default=get_current_env)
    # See the note on BotAppGrantModel.avernet_tenant: server_default so
    # create_all emits the same DEFAULT the out-of-band DDL applies.
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # The tenant leads the key: a user id is meaningful only within a
        # tenant, and ``env`` is in it so one authorization cannot collide
        # across environments sharing a database.
        UniqueConstraint(
            "avernet_tenant",
            "app_id",
            "user_id",
            "env",
            name="uk_user_app_grant_scope",
        ),
        # The user's view — "which applications may act as me?". The unique key
        # reaches ``app_id`` before ``user_id``, so it cannot serve a lookup
        # that names no application.
        Index(
            "idx_user_app_grant_user",
            "avernet_tenant",
            "user_id",
            "env",
        ),
    )

    def to_record(self) -> UserAppGrantRecord:
        """Convert to the Pydantic record the service returns."""
        return UserAppGrantRecord(
            id=self.id,
            app_id=self.app_id,
            app_name=self.app_name,
            user_id=self.user_id,
            avernet_tenant=self.avernet_tenant,
            env=self.env,
            gmt_create=self.gmt_create,
        )


class UserAppGrantLogModel(Base):
    """Append-only history — one row per grant and per withdrawal, never updated.

    **No unique constraint, deliberately.** Accepting every event is the
    contract; a key on the scope columns would reintroduce the collision the
    two-table split exists to remove.
    """

    __tablename__ = "ac_user_app_grant_log"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True)
    app_id = Column(AutoIncrementBigInteger, nullable=False)
    app_name = Column(String(1024), nullable=False)
    user_id = Column(String(256), nullable=False)
    action = Column(
        String(32),
        nullable=False,
        comment=f"{UserGrantAction.GRANTED} | {UserGrantAction.REVOKED}",
    )
    env = Column(String(20), nullable=False, default=get_current_env)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        # Reconstruct one user's authorization history in order.
        Index(
            "idx_user_app_grant_log_user",
            "avernet_tenant",
            "user_id",
            "env",
            "gmt_create",
        ),
    )


# Confine every read/update/delete to the request's tenant and stamp it on every
# insert. The log's registration matters as much as the live table's: it is
# read after the live row is deleted, so it has no guarded parent to inherit
# isolation from at the moment it matters most.
register_avernet_tenant_guard(UserAppGrantModel)
register_avernet_tenant_guard(UserAppGrantLogModel)


__all__ = [
    "APP_NAME_MAX_LENGTH",
    "IDENTITY_MAX_LENGTH",
    "UserAppGrantLogModel",
    "UserAppGrantModel",
    "UserAppGrantRecord",
    "UserGrantAction",
]
