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
    """The mechanism that carries the secret to the source (W3, #1471).

    Members, not bare strings, so a caller sending an unimplemented one is
    refused *by name* ("reserved") rather than as an unknown value.

    Two are implemented. ``header`` **presents** an opaque secret — the value
    itself travels on the wire under a header the caller names. ``oss_aksk``
    **signs**: the secret key never leaves the platform, and what travels is a
    signature computed from it over the request. That difference is why the two
    carry different fields and why the key id, unlike a header secret, is
    readable back — it is an identifier that rotation cannot be operated
    without seeing.
    """

    HEADER = "header"
    OSS_AKSK = "oss_aksk"
    BASIC = "basic"


#: The mechanisms with no implementation yet — refused at write. ``oss_aksk``
#: has left this tuple: it is the object store's real road now.
RESERVED_TYPES = (CredentialType.BASIC,)

#: What each implemented mechanism requires, and what it may not carry. Read by
#: the service rather than re-derived per branch, so "which fields belong to
#: which mechanism" has one answer — the same discipline the source matrix
#: applies to categories and protocols.
REQUIRED_FIELDS_BY_TYPE: dict[CredentialType, frozenset[str]] = {
    CredentialType.HEADER: frozenset({"header_name"}),
    # ``endpoint`` joins ``access_key_id`` as required: it is issued with the
    # key pair and belongs to the same trust boundary. Keeping it here rather
    # than on the source is what makes the host a property of the credential
    # instead of something a tenant's document chooses.
    #
    # ``region`` is required too. The object store's signature version 4
    # scopes every signature to a region and the SDK refuses to sign without
    # one, so a credential stored without it would be accepted here and fail
    # every apply — the one thing this surface must never do.
    CredentialType.OSS_AKSK: frozenset({"access_key_id", "endpoint", "region"}),
}

#: Fields that belong to exactly one mechanism. Sending one on another is
#: refused rather than ignored: a caller who writes ``access_key_id`` on a
#: header credential believes they pointed it at an object store, and silence
#: would let them believe it until a fetch failed.
EXCLUSIVE_FIELDS_BY_TYPE: dict[CredentialType, frozenset[str]] = {
    CredentialType.HEADER: frozenset({"header_name"}),
    CredentialType.OSS_AKSK: frozenset({"access_key_id", "region", "endpoint"}),
}


class SourceCredentialRecord(BaseModel):
    """The public, redacted view — the only shape the API ever returns.

    ``secret_ciphertext`` intentionally has no public representation: read
    paths answer ``has_secret`` (and the header name to present *under*),
    never the value, per the #1469/#1471 acceptance.
    """

    id: int | None = None
    name: str
    credential_type: CredentialType = CredentialType.HEADER
    #: ``None`` on a mechanism that presents no header at all — an
    #: ``oss_aksk`` credential is handed to an object-store client rather than
    #: put on a request, so there is nothing the caller chose to report back.
    header_name: str | None = None
    #: ``oss_aksk`` only. Present in every read: an identifier, not a secret,
    #: and rotation is unoperable without it. Its partner
    #: (``secret_ciphertext``) still has no public representation at all.
    access_key_id: str | None = None
    #: ``oss_aksk`` only. Passed to the object-store client; ``None`` lets the
    #: client take its own default.
    region: str | None = None
    #: ``oss_aksk`` only. The object store this credential is for. Readable
    #: back for the same reason ``access_key_id`` is — it is an address, not a
    #: secret, and rotation cannot be operated blind.
    endpoint: str | None = None
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
    header_name: str = ""
    access_key_id: str | None = None
    region: str | None = None
    endpoint: str | None = None
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
    #: ``oss_aksk`` only, nullable and never backfilled: on a ``header`` row
    #: both of these describe nothing, and a defaulted value would read as
    #: configuration while governing nothing.
    access_key_id = Column(String(256), nullable=True)
    region = Column(String(64), nullable=True)
    endpoint = Column(String(512), nullable=True)
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
            access_key_id=self.access_key_id,
            region=self.region,
            endpoint=self.endpoint,
            allowed_prefixes=self.allowed_prefixes,
            secret_ciphertext=self.secret_ciphertext,
            owner_app_id=self.owner_app_id,
            modifier=self.modifier or "",
            gmt_modified=self.gmt_modified,
        )


# Confines every read to the request's tenant and stamps it on insert; the
# registrar validates that the mapped column exists.
register_avernet_tenant_guard(SourceCredentialModel)
