"""Provenance of one active Installation; missing provenance means historical unknown."""

from enum import StrEnum

from sqlalchemy import BigInteger, Column, ForeignKey, String
from sqlalchemy.dialects import mysql

from agentclaw.community.core.base import Base
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class SkillInstallationSourceKind(StrEnum):
    DIRECT = "DIRECT"
    DEFAULT = "DEFAULT"
    SKILL_SET = "SKILL_SET"


class BotSkillInstallationSource(Base):
    """Same-transaction provenance, not a second active-state or runtime ledger.

    Set ids deliberately have no cascading FK: removing a Default member or
    Set must leave the old source available for the next effective read.
    """

    __tablename__ = "ac_bot_skill_installation_source"
    installation_id = Column(
        BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql"),
        ForeignKey("ac_bot_skill_installation.id", ondelete="CASCADE"), primary_key=True,
        autoincrement=False,
    )
    source_kind = Column(String(16), nullable=False)
    source_set_id = Column(BigInteger, nullable=True)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")


register_avernet_tenant_guard(BotSkillInstallationSource)
