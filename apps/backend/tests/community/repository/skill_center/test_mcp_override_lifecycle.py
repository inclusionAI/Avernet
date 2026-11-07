"""Bot MCP overrides survive capability changes through real repository transactions."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.core.models.skill import SkillSet
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)


class _Database:
    def __init__(self, path) -> None:
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def orm_session(self):
        with self.transactional_orm_session() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        with self.sessions() as session:
            with session.begin():
                yield session


_SCOPE = {"bot_id": "bot", "owner_id": "owner", "engine_type": "openclaw"}
_CODE = "mcp.saved"
_CONFIG = {"headers": {"X-Project": "saved"}, "endpoint_env": "PRE"}


def _seed_set(db, *, active=True, default=False):
    with db.transactional_orm_session() as session:
        row = SkillSet(
            name="tools", user_id="" if default else "owner",
            bolt_id="" if default else "bot", engine_type="openclaw",
            is_active=active, is_default=default, env="dev",
        )
        session.add(row)
        session.flush()
        return str(row.id)


def _save_override(db, *, bot_id="bot", owner_id="owner", config=_CONFIG):
    import json

    with db.transactional_orm_session() as session:
        session.add(BotMCPConfig(
            bot_id=bot_id, owner_id=owner_id, server_code=_CODE,
            config=json.dumps(config), env="dev",
        ))


@pytest.mark.parametrize("active", [True, False])
def test_removing_a_member_and_deleting_its_set_preserves_bot_config(tmp_path, active):
    path = tmp_path / "saved.sqlite"
    db = _Database(path)
    repo = CapabilityDesiredStateRepository(db)
    set_id = _seed_set(db, active=active)
    repo.add_mcp(
        **_SCOPE, set_id=set_id, server_code=_CODE,
        platform_default_codes=frozenset(), name="Saved", description=None, icon=None,
    )
    _save_override(db)

    assert repo.remove_mcp(**_SCOPE, set_id=set_id, server_code=_CODE).changed
    assert repo.list_mcps(**_SCOPE, set_id=set_id) == []
    assert repo.list_installed_mcps(**_SCOPE) == set()
    assert repo.get_mcp_overrides(bot_id="bot", owner_id="owner") == {_CODE: _CONFIG}
    repo.set_skill_set_active(**_SCOPE, set_id=set_id, active=False)
    repo.delete_set(**_SCOPE, set_id=set_id)
    db.engine.dispose()

    rebuilt = CapabilityDesiredStateRepository(_Database(path))
    assert rebuilt.list_sets(**_SCOPE) == []
    assert rebuilt.get_mcp_overrides(bot_id="bot", owner_id="owner") == {_CODE: _CONFIG}


def test_default_exclusion_and_reconciliation_preserve_config(tmp_path):
    from agentclaw.community.core.models.mcp import SkillSetMCPServer

    db = _Database(tmp_path / "default.sqlite")
    repo = CapabilityDesiredStateRepository(db)
    set_id = _seed_set(db, default=True)
    with db.transactional_orm_session() as session:
        session.add(SkillSetMCPServer(
            skill_set_id=int(set_id), server_code=_CODE, name="Saved", env="dev",
        ))
    _save_override(db)
    repo.flush_installations(**_SCOPE, env="dev")
    assert repo.list_installed_mcps(**_SCOPE) == {_CODE}

    assert repo.exclude_default_mcp(**_SCOPE, set_id=set_id, server_code=_CODE).changed
    for _ in range(2):
        repo.flush_installations(**_SCOPE, env="dev")
    assert repo.list_installed_mcps(**_SCOPE) == set()
    assert repo.get_mcp_overrides(bot_id="bot", owner_id="owner") == {_CODE: _CONFIG}
    assert repo.unexclude_default_mcp(
        **_SCOPE, set_id=set_id, server_code=_CODE, platform_default_codes=frozenset()
    ).changed
    assert repo.list_installed_mcps(**_SCOPE) == {_CODE}
    assert repo.get_mcp_overrides(bot_id="bot", owner_id="owner") == {_CODE: _CONFIG}


@pytest.mark.parametrize("installed_before_snapshot", [False, True])
def test_capability_restore_keeps_configuration_saved_after_snapshot(
    tmp_path, installed_before_snapshot
):
    db = _Database(tmp_path / "restore.sqlite")
    repo = CapabilityDesiredStateRepository(db)
    if installed_before_snapshot:
        repo.set_mcp_override(
            **_SCOPE, server_code=_CODE, config=_CONFIG,
            platform_default_codes=frozenset(),
        )
    snapshot = repo.snapshot_desired_state(**_SCOPE)
    current_config = {"headers": {"X-Project": "edited-after-snapshot"}}
    repo.set_mcp_override(
        **_SCOPE, server_code=_CODE, config=current_config,
        platform_default_codes=frozenset(),
    )

    repo.restore_desired_state(**_SCOPE, state=snapshot)

    assert repo.list_installed_mcps(**_SCOPE) == (
        {_CODE} if installed_before_snapshot else set()
    )
    assert repo.get_mcp_overrides(bot_id="bot", owner_id="owner") == {
        _CODE: current_config
    }
