"""Persistence model and redacted public record for source credentials (W3).

One row per ``(avernet_tenant, name)`` — the issue's key, deliberately
without an ``env`` axis: a credential is a *tenant-level* object (one
presentation token for one content host), not a per-environment secret.
Cross-environment isolation is carried by nothing here, which is exactly
the point: pre and prod share the tenant's git token or they don't, and a
row-splitting ``env`` column would silently answer that question wrong.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY KEY".
# BigInteger renders as "BIGINT" in SQLite, which breaks autoincrement.
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")


class CredentialType(StrEnum):
    """The presentation mechanism carrying the secret (W3, #1471).

    Members, not bare strings: 'header' is the one implemented mechanism
    and the only value a write can store; 'oss_aksk'/'basic' are reserved
    containers for future mechanisms — kept in the vocabulary so a caller
    sending one is refused *by name* ("reserved") rather than as an
    unknown value, per the one-endpoint-one-body discrimination.
    """

    HEADER = "header"
    OSS_AKSK = "oss_aksk"
    BASIC = "basic"


#: The mechanisms with no implementation yet — refused at write.
RESERVED_TYPES = (CredentialType.OSS_AKSK, CredentialType.BASIC)


class SourceCredentialRecord(BaseModel):
    """The public, redacted view — the only shape the API ever returns.

    ``secret_ciphertext`` intentionally has no public representation: read
    paths answer ``has_secret`` (and the header name to present *under*),
    never the value, per the #1469/#1471 acceptance.
    """

    id: int | None = None
    name: str
    credential_type: CredentialType = CredentialType.HEADER
    header_name: str
    allowed_prefixes: list[str]
    has_secret: bool = True
    #: The owning application (registry id). Rotation and delete are its
    #: alone; every tenant application may read the masked metadata.
    owner_app_id: int
    updated_at: datetime


class SourceCredentialRow(BaseModel):
    """The storage-shaped record: everything a row holds, ciphertext included.

    Constructed inside the repository's session (detached rows lose lazy
    attributes); the masked public view is built from this — the two
    records exist so the value physically cannot end up in the public one
    by construction, not by discipline.
    """

    id: int | None = None
    name: str
    credential_type: CredentialType
    header_name: str
    allowed_prefixes: str  # JSON array as stored
    secret_ciphertext: str
    #: Set at insert, immutable after: the application whose PUT created
    #: the name. Router-level ownership (rotation/delete) reads it.
    owner_app_id: int
    modifier: str
    gmt_modified: datetime


class SourceCredentialModel(Base):
    """``ac_source_credential`` row; tenant guard owns the isolation."""

    __tablename__ = "ac_source_credential"

    id = Column(
        AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    #: Tenant isolation boundary — in the uniqueness key, not an optional
    #: filter (same doctrine as ``ac_bot_startup_script``: the visible key
    #: beats a hashed-in one).
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    name = Column(String(128), nullable=False)
    credential_type = Column(String(32), nullable=False)
    header_name = Column(String(256), nullable=False)
    #: JSON array of absolute https prefixes, validated at write time.
    allowed_prefixes = Column(Text, nullable=False)
    #: ``enc:v1:<AES-GCM ciphertext>`` (or plaintext under a non fail-closed
    #: profile with no master key — never both meanings for one profile).
    secret_ciphertext = Column(Text, nullable=False)
    #: The owning application — the creating PUT's caller app id. Written
    #: at insert only and never re-stamped: rotation and delete are the
    #: owner's alone, and a re-PUT by another application is refused
    #: before storage.
    owner_app_id = Column(BigInteger, nullable=False)
    modifier = Column(String(1024), nullable=False, server_default="")
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant", "name", name="uk_tenant_source_credential_name"
        ),
    )

    def to_row(self) -> SourceCredentialRow:
        """Detachment-safe handoff: row attributes inside the session."""
        return SourceCredentialRow(
            id=self.id,
            name=self.name,
            credential_type=self.credential_type,
            header_name=self.header_name,
            allowed_prefixes=self.allowed_prefixes,
            secret_ciphertext=self.secret_ciphertext,
            owner_app_id=self.owner_app_id,
            modifier=self.modifier or "",
            gmt_modified=self.gmt_modified,
        )


# Confines every read to the request's tenant and stamps it on insert; the
# registrar validates that the mapped column exists.
register_avernet_tenant_guard(SourceCredentialModel)
