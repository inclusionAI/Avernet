from __future__ import annotations

from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import BigInteger, Column, Index, Integer, String, Text

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.repository.base import (
    _AgentSwarmBase, _ID128,
)


class AcAgentSwarmAgentDefinitionModel(_AgentSwarmBase, Base):
    """One immutable version of a user-owned agent definition.

    A definition is identified by ``definition_id``, which is **reused across
    every version**; a version is identified by the row's own ``id``. An update
    inserts a successor row and retires the predecessor by setting
    ``is_deleted`` to that predecessor's ``id`` (the ``baas_bot`` multi-version
    pattern), so the live-row unique key below permits exactly one live version
    and any number of retired ones.
    """

    __tablename__ = "ac_agent_swarm_agent_definition"

    definition_id = Column(_ID128, nullable=False)
    name = Column(String(128), nullable=False)
    # Short, author-written catalog copy: it is folded into the bot record's
    # description, never used as a prompt. 512 leaves room for a real
    # multi-sentence role while staying a fixed-width indexable column.
    role = Column(String(512), nullable=False, default="")
    instructions = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    kind = Column(String(16), nullable=False, default="llm")
    model = Column(String(128), nullable=True)
    engine = Column(String(64), nullable=True)
    bot_type = Column(String(32), nullable=False, default="personal")
    manifest_document = Column(Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True)
    # `sha256:<hex64>` of manifest_document; empty means "no manifest", exactly
    # as manifest_document does. The `sha256:` prefix is the codebase's fixed
    # digest vocabulary (`_DIGEST_RE`), so this value is comparable with every
    # other digest consumer.
    manifest_digest = Column(String(128), nullable=False, default="")
    # owner_id authorizes; creator_id authored the definition and is copied
    # forward on each version; modifier_id produced this version. All three are
    # the authenticated caller's staff id, never a request parameter.
    owner_id = Column(String(64), nullable=False)
    creator_id = Column(String(64), nullable=False)
    modifier_id = Column(String(64), nullable=False)

    # Tombstone id, so this column must hold a row id rather than a boolean -
    # hence the BigInteger override of the mixin's tinyint(1).
    is_deleted = Column(BigInteger, nullable=False, default=0, server_default="0")

    __table_args__ = (
        Index(
            "uk_def_live",
            "env", "tenant", "definition_id", "is_deleted",
            unique=True,
        ),
        Index(
            "uk_owner_name",
            "env", "tenant", "owner_id", "name", "is_deleted",
            unique=True,
        ),
        Index("idx_owner_live", "env", "tenant", "owner_id", "is_deleted"),
    )