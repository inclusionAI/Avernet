"""Contract checks keeping the Space bootstrap DDL aligned with its ORM model."""

from pathlib import Path
import re

from sqlalchemy import UniqueConstraint

from agentclaw.community.core.spaces.repository.models import SpaceModel


_DDL_PATH = (
    Path(__file__).parents[4]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "spaces"
    / "sql"
    / "2026_08_17_spaces.sql"
)


def _normalized_ddl() -> str:
    return re.sub(r"\s+", " ", _DDL_PATH.read_text(encoding="utf-8"))


def test_sc_team_id_contract_is_present_in_orm_and_bootstrap_ddl() -> None:
    column = SpaceModel.__table__.c.sc_team_id
    assert column.type.length == 64
    assert column.nullable is True

    unique_constraints = {
        constraint.name: tuple(item.name for item in constraint.columns)
        for constraint in SpaceModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints["uk_sc_team_id_env"] == ("sc_team_id", "env")

    ddl = _normalized_ddl()
    assert (
        "sc_team_id VARCHAR(64) DEFAULT NULL "
        "COMMENT 'SkillCenter团队ID，团队空间同步SC成功后写入'"
    ) in ddl
    assert "UNIQUE KEY uk_sc_team_id_env (sc_team_id, env)" in ddl
