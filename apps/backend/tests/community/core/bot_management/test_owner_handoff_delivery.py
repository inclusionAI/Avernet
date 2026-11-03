"""Worker scope, disabled rollout wiring, and capability contract boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    OwnerHandoffPolicy,
)
from agentclaw.community.core.bot_management.owner_handoff.tasks import (
    OwnerHandoffTask,
    OwnerHandoffLifecycle,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Reschedule, Retry, Fail
from agentclaw.community.di.modules.owner_handoff_module import parse_handoff_policy
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


@pytest.mark.parametrize(
    "block,queue",
    [
        ({"enabled": "false"}, True),
        ({"unknown": True}, True),
        ({"enabled": True}, True),
        ({"recovery_enabled": True}, False),
        (None, True),
    ],
)
def test_invalid_handoff_config_is_rejected(block, queue):
    with pytest.raises(ValueError):
        parse_handoff_policy(block, queue)


def test_disabled_defaults_and_recovery_only_policy():
    assert parse_handoff_policy({}, False).enabled is False
    policy = parse_handoff_policy({"recovery_enabled": True}, True)
    assert policy.recovery_enabled and not policy.enabled


@pytest.mark.parametrize("done,kind", [(True, Complete), (False, Reschedule)])
def test_worker_restores_explicit_tenant_and_returns_phase_outcome(done, kind):
    seen = []

    def step(operation):
        seen.append((operation, get_current_avernet_tenant()))
        return done

    handler = OwnerHandoffTask(SimpleNamespace(step=step))
    result = handler.handle({"operation_id": "op", "avernet_tenant": "external"})
    assert isinstance(result, kind)
    assert seen == [("op", "external")]
    assert get_current_avernet_tenant() == "teamclaw"


def test_worker_never_logs_or_returns_raw_storage_error():
    service = Mock()
    service.step.side_effect = RuntimeError("credential-secret")
    result = OwnerHandoffTask(service).handle(
        {"operation_id": "op", "avernet_tenant": "teamclaw"}
    )
    assert isinstance(result, Retry) and result.error == "HANDOFF_STORAGE_ERROR"
    assert isinstance(OwnerHandoffTask(service).handle({}), Fail)


@pytest.mark.asyncio
async def test_disabled_lifecycle_registers_handler_without_querying_journal():
    service = Mock()
    registry = HandlerRegistry()
    lifecycle = OwnerHandoffLifecycle(
        registry=registry, service=service, policy=OwnerHandoffPolicy()
    )
    await lifecycle.bootstrap()
    await lifecycle.startup()
    await lifecycle.shutdown()
    service.reconcile.assert_not_called()
    assert registry.get("bot_owner_handoff") is not None


@pytest.mark.asyncio
async def test_enabled_lifecycle_reconciles_on_boot_and_stops_loop():
    service = Mock()
    lifecycle = OwnerHandoffLifecycle(
        registry=HandlerRegistry(),
        service=service,
        policy=OwnerHandoffPolicy(recovery_enabled=True),
    )
    await lifecycle.startup()
    service.reconcile.assert_called_once_with()
    await lifecycle.shutdown()
