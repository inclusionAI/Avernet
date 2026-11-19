"""Wake owns root startup; construction and reconciliation do not start Work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver import (
    SwarmDriver,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    SwarmRuntimeConfiguration,
)


def _driver(status="created"):
    coordinator = ActorContext(PrincipalRef("human", "owner"), "swarm_driver")
    starter = ActorContext(PrincipalRef("bot", "executor"), "swarm_driver")
    resolver = Mock()
    resolver.resolve.return_value = SwarmRuntimeConfiguration(
        Mock(), coordinator, starter
    )
    store = Mock()
    store.list_bindings.return_value = [
        SimpleNamespace(
            method_names=("Swarm",),
            state=SimpleNamespace(value="ACTIVE"),
            method_binding_id="binding_swarm",
        )
    ]
    record = SimpleNamespace(
        collet=SimpleNamespace(version=7),
        task=SimpleNamespace(task_id="task_root", status=status),
    )
    reader = Mock()
    reader.get_current_status.return_value = record
    work = Mock()

    def start(*args):
        record.task.status = "in_progress"

    work.start.side_effect = start
    driver = SwarmDriver(
        "collet_test", store, Mock(), reader, resolver, Mock(), Mock(), work
    )
    return driver, work, record, starter


def test_construction_does_not_start_or_enqueue_work():
    driver, work, record, _ = _driver()
    work.start.assert_not_called()
    assert record.task.status == "created"
    assert not driver.pending and not driver.queue


def test_first_wake_starts_with_runtime_start_actor_then_coalesces():
    driver, work, record, starter = _driver()
    driver.wake()
    driver.wake()
    work.start.assert_called_once_with("collet_test", WorkStartCommand(7), starter)
    assert starter != driver.actor
    assert record.task.status == "in_progress"
    assert driver.pending and list(driver.queue) == [None]


def test_running_work_wake_only_enqueues_reconciliation():
    driver, work, _, _ = _driver("in_progress")
    driver.wake()
    work.start.assert_not_called()
    assert driver.pending and list(driver.queue) == [None]


def test_start_failure_propagates_without_queuing_and_can_be_retried():
    driver, work, record, starter = _driver()
    work.start.side_effect = ContextCollabValidationError("startup denied")
    with pytest.raises(ContextCollabValidationError, match="startup denied"):
        driver.wake()
    assert not driver.pending and not driver.queue
    assert record.task.status == "created"
    work.start.side_effect = None
    driver.wake()
    assert work.start.call_count == 2
    work.start.assert_called_with("collet_test", WorkStartCommand(7), starter)
    assert driver.pending and list(driver.queue) == [None]
