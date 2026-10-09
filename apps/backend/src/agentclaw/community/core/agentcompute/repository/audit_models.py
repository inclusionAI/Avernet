"""SQLAlchemy ORM model for ``ac_agent_compute_audit`` — append-only audit.

One row per authorized boundary outcome on the agentcompute HTTP surface
(D25 / design §17.5): accepted and rejected submits (with the admission
reason), and cancel requests. Written best-effort by
:class:`agentclaw.community.core.agentcompute.audit.AgentComputeAuditWriter`;
never updated or deleted — the writer exposes no mutation methods, which is
the enforcement shape (mirrors ``ac_governance_audit``).

The mandatory-column mix-in is defined locally rather than imported from
``repository/models.py``: ``models.py`` imports this module to append the
model to ``ALL_MODELS``-adjacent metadata, and the writer imports this module
directly — importing the shared base back from ``models`` would turn either
entry order into a circular import. The columns are kept character-for-
character identical to ``_AgentComputeBase`` so both layouts stay
interchangeable (see ``tests/.../test_storage.py`` mandatory-column gate).
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.utils.env_utils import get_current_env

AutoIncrementBigInteger = BigInteger().with_variant(Integer, "sqlite")

_ENV_LEN = 20
_TENANT_LEN = 64


class _AgentComputeAuditBase:
    """Mandatory columns — mirrors ``repository.models._AgentComputeBase``."""

    id = Column(AutoIncrementBigInteger, primary_key=True, autoincrement=True, nullable=False)
    env = Column(String(_ENV_LEN), nullable=False, default=get_current_env)
    tenant = Column(String(_TENANT_LEN), nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False, server_default="0")
    gmt_create = Column(DateTime, default=func.now(), nullable=False)
    gmt_modified = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


class AcAgentComputeAuditModel(_AgentComputeAuditBase, Base):
    """Append-only audit row for the agentcompute HTTP boundary.

    Domain columns (5, within the ≤10 budget from task 12.7):

    - ``actor`` — staffId of the authenticated principal that made the call
      (D25: identity from the principal, never the request body).
    - ``action`` — closed set (see
      ``core.agentcompute.audit.AuditAction``): ``submit_accepted`` |
      ``submit_rejected`` | ``cancel_requested``.
    - ``reason`` — admission/outcome discriminator for rejections, from the
      documented vocabulary: ``tenant_limit`` | ``global_limit`` |
      ``queue_depth`` | ``config_missing`` | ``not_found``.
    - ``run_id`` — nullable; set when the outcome is tied to a specific run.
    - ``source`` — entry surface, ``http`` (the CLI drives HTTP too).
    """

    __tablename__ = "ac_agent_compute_audit"

    actor = Column(String(128), nullable=False)
    action = Column(String(64), nullable=False)
    reason = Column(String(255), nullable=True)
    run_id = Column(String(128), nullable=True)
    source = Column(String(32), nullable=False, default="http")

    __table_args__ = (
        # Ops forensics: newest rows per (tenant, id); per-run history; and
        # actor review. All non-unique — append-only tables have no UK.
        Index("idx_audit_scope", "env", "tenant", "id"),
        Index("idx_audit_run", "env", "tenant", "run_id"),
        Index("idx_audit_actor", "env", "tenant", "actor"),
    )
