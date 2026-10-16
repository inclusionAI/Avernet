"""Native startup signals must not schedule historical migration work."""
from dataclasses import replace

import pytest

from agentclaw.community.core.events.types import BaasPublishCompletedEvent, DeviceAliveEvent
from agentclaw.community.core.skills_pool.types import RolloutEvidence, SkillLayout, SkillLayoutPhase
from tests.community.core.skills_pool.test_reconcile_task import _binding, _claimed_state, _listener


@pytest.mark.parametrize("phase", [SkillLayoutPhase.POOL_INITIALIZING, SkillLayoutPhase.POOL_ACTIVE])
@pytest.mark.parametrize("provider", ["arca", "baas"])
def test_pool_native_wakeup_does_not_enqueue_migration(phase, provider) -> None:
    listener, queue = _listener(
        binding=_binding(
            provider=provider,
            props={"sandbox_id": "sandbox-current", "restart_publish_id": "1002"},
        ),
    )
    listener._layouts.get.return_value = replace(
        _claimed_state(active_layout=SkillLayout.POOL),
        target_layout=None,
        phase=phase,
        migration_generation=None,
        preparation_id=None,
        rollout_evidence=RolloutEvidence(
            env="pre", config_id=7, config_version="revision-1", batch_id=None,
            engine_type="openclaw", decision_reason="owner_allowlist",
        ),
    )
    event = (
        DeviceAliveEvent(
            device_id="device-current", binding_id=42, entity_id="entity-1",
            entity_type="staff", device_provider="arca", sandbox_id="sandbox-current",
        )
        if provider == "arca"
        else BaasPublishCompletedEvent(
            binding_id=42, bot_id="bot-1", owner_id="owner-1",
            publish_id=1002, publish_kind="restart",
        )
    )
    listener.handle(event)
    queue.enqueue.assert_not_called()


def test_migrated_pool_wakeup_still_enqueues_reconciliation() -> None:
    listener, queue = _listener(
        binding=_binding(provider="arca", props={"sandbox_id": "sandbox-current"}),
    )
    listener._layouts.get.return_value = _claimed_state(active_layout=SkillLayout.POOL)
    listener.handle(DeviceAliveEvent(
        device_id="device-current", binding_id=42, entity_id="entity-1",
        entity_type="staff", device_provider="arca", sandbox_id="sandbox-current",
    ))
    queue.enqueue.assert_called_once()
