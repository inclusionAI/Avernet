"""Committed capability changes and recovery records are one transaction."""

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_projection import (
    CapabilityProjectionRepository,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import ProjectionScope

from .capability_desired_state_support import _Database


def test_claim_rolls_back_when_pending_projection_cannot_be_persisted():
    db = _Database()
    desired = CapabilityDesiredStateRepository(db)
    projection = CapabilityProjectionRepository(db)
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_projection
            BEFORE INSERT ON ac_bot_capability_projection
            BEGIN
              SELECT RAISE(ABORT, 'cannot write projection');
            END
            """
        )

    with pytest.raises(IntegrityError, match="cannot write projection"):
        desired.commit_projection_mutation(
            bot_id="bot",
            entity_id="owner",
            mutation=lambda: desired.claim_apply_mcp(
                bot_id="bot",
                owner_id="owner",
                server_code="new-server",
                config={},
                platform_default_codes=frozenset(),
            ),
            scope_from_result=lambda result: ProjectionScope(
                mcp=True, claimed_mcp=result.mcp_codes,
            ),
            retired_mappings=(),
            create=True,
        )

    assert desired.list_installed_mcps(bot_id="bot", owner_id="owner") == set()
    assert projection.read(bot_id="bot", entity_id="owner") is None
