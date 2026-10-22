"""ORM models + row records for the five ``ac_genome_*`` tables (RSI-03).

Registered on ``core.base.Base`` so the ``create_all`` bootstrap emits them;
the side-effect import lives in ``core/schema.py``. The DDL with the full
column commentary is ``core/bot_genome/sql/2026_10_19_bot_genome.sql``; the
two must agree column for column (test-pinned).

Every table carries the bot axes ``(avernet_tenant, env, entity_id, bot_id)``
with the tenant guard, exactly as ``ac_manifest_content`` does: a ``bot_id``
is unique only within a tenant, and ``entity_id`` is the storage key shared
with the Manifest tables (256 wide for the index-budget reason recorded
there).

- ``ac_genome_revision``: one row per revision; ``content`` holds the exact
  hashed bytes (``JCS({"policy": …, "spec": …})``) so a reader can re-verify
  the id; only ``status``, ``evaluations`` and ``annotations`` ever change.
- ``ac_genome_ref``: one row per ``(bot, name)``, moved by compare-and-swap
  on the revision it points at; ``version`` counts moves.
- ``ac_genome_ref_event``: the append-only move log.
- ``ac_genome_patch``: content-addressed; the digest is the candidate id.
- ``ac_genome_promotion``: one row per promotion, with the compiled document
  that was handed to delivery and its digest.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import (
    register_avernet_tenant_guard,
)
from agentclaw.community.utils.env_utils import get_current_env

# SQLite only auto-increments columns declared as exactly "INTEGER PRIMARY KEY".
AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")
# MEDIUMTEXT on MySQL/OceanBase (a 16 MiB document); TEXT elsewhere.
MediumText = Text().with_variant(mysql.MEDIUMTEXT, "mysql")

DIGEST_WIDTH = 71  # "sha256:" + 64 hex


# ---------------------------------------------------------------------------------------------
# Row records


class GenomeRevisionRow(BaseModel):
    """One ``ac_genome_revision`` row. ``evidence``/``evaluations``/``annotations`` are JSON text."""

    id: Optional[int] = None
    env: str
    entity_id: str
    bot_id: str
    revision_id: str
    seq: int = 0  # assigned by the repository on insert
    parent_id: Optional[str] = None
    genome_schema: int
    status: str
    created_kind: str
    created_actor: str
    run_id: Optional[str] = None
    run_step: Optional[str] = None
    patch_digest: Optional[str] = None
    evidence: str = "[]"
    evaluations: str = "[]"
    annotations: str = "{}"
    content: str
    created_at: datetime
    gmt_create: datetime = Field(default_factory=datetime.now)
    gmt_modified: datetime = Field(default_factory=datetime.now)


class GenomeRefRow(BaseModel):
    """One ``ac_genome_ref`` row: where a name points now."""

    id: Optional[int] = None
    env: str
    entity_id: str
    bot_id: str
    name: str
    revision_id: str
    version: int = 1
    moved_at: datetime
    moved_kind: str
    moved_actor: str
    run_id: Optional[str] = None
    reason: Optional[str] = None


class GenomeRefEventRow(BaseModel):
    """One ``ac_genome_ref_event`` row: one move."""

    id: Optional[int] = None
    env: str
    entity_id: str
    bot_id: str
    name: str
    from_revision_id: Optional[str]
    to_revision_id: str
    moved_kind: str
    moved_actor: str
    run_id: Optional[str] = None
    reason: Optional[str] = None
    moved_at: datetime


class CandidateRefRequest(BaseModel):
    """What ``GenomeRevisionRepositoryProtocol.record`` needs to name a run's candidate ref in the same transaction."""

    run_id: str
    moved_kind: str
    moved_actor: str
    moved_at: datetime


class GenomePatchRow(BaseModel):
    """One ``ac_genome_patch`` row: the canonical JSON of a recorded patch."""

    id: Optional[int] = None
    env: str
    entity_id: str
    bot_id: str
    digest: str
    base_id: str
    content: str
    created_at: datetime


class GenomePromotionRow(BaseModel):
    """One ``ac_genome_promotion`` row."""

    id: Optional[int] = None
    env: str
    entity_id: str
    bot_id: str
    promotion_id: str
    ref: str
    revision_id: str
    from_revision_id: Optional[str] = None
    document_digest: str
    document_json: str
    going_back: bool = False
    actor_kind: str
    actor_id: str
    reason: str
    status: str
    apply_kind: str
    apply_reference: Optional[str] = None
    apply_detail: str = ""
    created_at: datetime
    gmt_create: datetime = Field(default_factory=datetime.now)
    gmt_modified: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------------------------
# ORM


def _bot_axes() -> list[Column]:
    """The four columns every genome table leads with (tenant stamped by the guard)."""
    return [
        Column("env", String(20), nullable=False, default=get_current_env, comment="环境标识: prod/pre/dev"),
        Column("entity_id", String(256), nullable=False, comment="实体ID（bot 的 entity_id）"),
        Column("bot_id", String(256), nullable=False, comment="Bot ID"),
        Column("avernet_tenant", String(64), nullable=False, server_default="teamclaw"),
    ]


class GenomeRevisionModel(Base):
    __tablename__ = "ac_genome_revision"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env, entity_id, bot_id, avernet_tenant = _bot_axes()
    revision_id = Column(CHAR(DIGEST_WIDTH), nullable=False, comment="sha256 of JCS({policy, spec})")
    seq = Column(Integer, nullable=False, comment="per-bot sequence number, for humans (r42)")
    parent_id = Column(CHAR(DIGEST_WIDTH), nullable=True, comment="the revision this one was derived from")
    genome_schema = Column(Integer, nullable=False, comment="contract version (1)")
    status = Column(String(16), nullable=False, comment="draft/candidate/accepted/rejected/promoted/archived")
    created_kind = Column(String(16), nullable=False, comment="user/strategy_run")
    created_actor = Column(String(1100), nullable=False, comment="who recorded it; an app acting for a user is app:<id>:on-behalf-of:<user>")
    run_id = Column(String(64), nullable=True, comment="the evolution run, for strategy_run")
    run_step = Column(String(64), nullable=True, comment="the run step")
    patch_digest = Column(CHAR(DIGEST_WIDTH), nullable=True, comment="the patch that produced it")
    evidence = Column(Text, nullable=False, default="[]", comment="JSON list of evidence refs")
    evaluations = Column(Text, nullable=False, default="[]", comment="JSON list of evaluation ids")
    annotations = Column(Text, nullable=False, default="{}", comment="JSON object")
    content = Column(MediumText, nullable=False, comment='JCS({"policy":…,"spec":…}), the hashed bytes')
    created_at = Column(DateTime, nullable=False, comment="record time, UTC")
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("avernet_tenant", "env", "entity_id", "bot_id", "revision_id", name="uk_bot_revision"),
        UniqueConstraint("avernet_tenant", "env", "entity_id", "bot_id", "seq", name="uk_bot_seq"),
        Index("idx_genome_rev_bot_parent", "avernet_tenant", "env", "entity_id", "bot_id", "parent_id"),
    )

    def to_record(self) -> GenomeRevisionRow:
        return GenomeRevisionRow.model_validate(self, from_attributes=True)


class GenomeRefModel(Base):
    __tablename__ = "ac_genome_ref"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env, entity_id, bot_id, avernet_tenant = _bot_axes()
    name = Column(String(160), nullable=False, comment="active/previous/canary/draft/candidate/<run>/<n>")
    revision_id = Column(CHAR(DIGEST_WIDTH), nullable=False, comment="where the ref points")
    version = Column(BigInteger, nullable=False, default=1, comment="moves so far; CAS token")
    moved_at = Column(DateTime, nullable=False)
    moved_kind = Column(String(16), nullable=False, comment="user/strategy_run")
    moved_actor = Column(String(1100), nullable=False)
    run_id = Column(String(64), nullable=True)
    reason = Column(String(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint("avernet_tenant", "env", "entity_id", "bot_id", "name", name="uk_bot_ref"),
    )

    def to_record(self) -> GenomeRefRow:
        return GenomeRefRow.model_validate(self, from_attributes=True)


class GenomeRefEventModel(Base):
    __tablename__ = "ac_genome_ref_event"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env, entity_id, bot_id, avernet_tenant = _bot_axes()
    name = Column(String(160), nullable=False)
    from_revision_id = Column(CHAR(DIGEST_WIDTH), nullable=True, comment="NULL when the ref was created")
    to_revision_id = Column(CHAR(DIGEST_WIDTH), nullable=False)
    moved_kind = Column(String(16), nullable=False)
    moved_actor = Column(String(1100), nullable=False)
    run_id = Column(String(64), nullable=True)
    reason = Column(String(1024), nullable=True)
    moved_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_genome_ref_event_bot_ref", "avernet_tenant", "env", "entity_id", "bot_id", "name", "moved_at"),
    )

    def to_record(self) -> GenomeRefEventRow:
        return GenomeRefEventRow.model_validate(self, from_attributes=True)


class GenomePatchModel(Base):
    __tablename__ = "ac_genome_patch"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env, entity_id, bot_id, avernet_tenant = _bot_axes()
    digest = Column(CHAR(DIGEST_WIDTH), nullable=False, comment="sha256 of JCS(patch): the candidate id")
    base_id = Column(CHAR(DIGEST_WIDTH), nullable=False, comment="the revision the patch was written against")
    content = Column(MediumText, nullable=False, comment="JCS(patch)")
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("avernet_tenant", "env", "entity_id", "bot_id", "digest", name="uk_bot_patch"),
    )

    def to_record(self) -> GenomePatchRow:
        return GenomePatchRow.model_validate(self, from_attributes=True)


class GenomePromotionModel(Base):
    __tablename__ = "ac_genome_promotion"

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env, entity_id, bot_id, avernet_tenant = _bot_axes()
    promotion_id = Column(CHAR(32), nullable=False, comment="uuid4 hex")
    ref = Column(String(16), nullable=False, comment="active/canary")
    revision_id = Column(CHAR(DIGEST_WIDTH), nullable=False)
    from_revision_id = Column(CHAR(DIGEST_WIDTH), nullable=True, comment="where the ref pointed before")
    document_digest = Column(CHAR(DIGEST_WIDTH), nullable=False, comment="sha256 of JCS(compiled document)")
    document_json = Column(MediumText, nullable=False, comment="the compiled document handed to delivery, canonical JSON")
    going_back = Column(Boolean, nullable=False, default=False, comment="the target had been promoted before")
    actor_kind = Column(String(16), nullable=False, comment="user/strategy_run")
    actor_id = Column(String(1100), nullable=False)
    reason = Column(String(1024), nullable=False)
    status = Column(String(16), nullable=False, comment="applying/applied/apply_failed")
    apply_kind = Column(String(16), nullable=False, comment="the applier that took it (pending/direct/…)")
    apply_reference = Column(String(64), nullable=True, comment="the applier's handle (an apply id)")
    apply_detail = Column(String(1024), nullable=False, default="", comment="the applier's last word")
    created_at = Column(DateTime, nullable=False)
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("avernet_tenant", "promotion_id", name="uk_genome_promotion"),
        Index("idx_genome_promotion_bot_apply", "avernet_tenant", "env", "entity_id", "bot_id", "apply_reference"),
        Index("idx_genome_promotion_bot_created", "avernet_tenant", "env", "entity_id", "bot_id", "created_at"),
    )

    def to_record(self) -> GenomePromotionRow:
        return GenomePromotionRow.model_validate(self, from_attributes=True)


for _model in (GenomeRevisionModel, GenomeRefModel, GenomeRefEventModel, GenomePatchModel, GenomePromotionModel):
    # Confines every read to the request's tenant and stamps it on insert.
    register_avernet_tenant_guard(_model)
