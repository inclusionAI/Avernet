from __future__ import annotations

import logging

import pytest

from agentclaw.community.core.repository.capability_desired_state_types import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.services._mutation_flow import (
    MutationProjectionFlow,
)


class _Repository:
    def restore_desired_state(self, **_kwargs) -> None:
        raise AssertionError("restore is not expected on the success path")


class _Runtime:
    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def project(self, **_kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_mutation_flow_logs_control_plane_timing_stages(caplog) -> None:
    flow = MutationProjectionFlow(repository=_Repository(), runtime=_Runtime())
    caplog.set_level(logging.INFO)

    result = await flow.apply(
        bot={"owner_id": "owner-1", "status": "ACTIVE"},
        bot_id="bot-1",
        engine_type="openclaw",
        scope=ProjectionScope(skills=True),
        mutation=lambda: DesiredStateMutation(
            item={"id": "set-1"},
            changed=True,
            previous_state=CapabilityDesiredState(
                installations=set(),
                set_active={},
                memberships={},
            ),
        ),
    )

    assert result == {"id": "set-1", "changed": True}
    messages = [record.getMessage() for record in caplog.records]
    for stage in ("snapshot_before", "mutation_tx", "snapshot_after"):
        assert any(
            "[MutationProjectionFlow] timing" in message
            and f"stage={stage}" in message
            and "bot_id=bot-1" in message
            and "duration_ms=" in message
            for message in messages
        )
