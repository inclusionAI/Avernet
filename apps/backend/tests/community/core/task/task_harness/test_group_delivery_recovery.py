"""Manager-worker failure confirmation through the existing participant API."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, RuntimeInfo, Status, TaskInfo, TaskNode,
    TaskNodePatch, TaskSpec,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_harness.harness import TaskHarness
from agentclaw.community.core.task.task_harness.relay_session_recovery import RelaySessionRecovery
from agentclaw.community.core.task.task_harness.runtime_probe import TaskRuntimeLivenessProbe, _last_message


class Bcs:
    def __init__(self):
        self.messages = [
            {"id": "z-source", "timestamp": 1000, "message_type": "system", "content": "task"},
            {"id": "a-error", "timestamp": 2000, "message_type": "chat_error",
             "sender": "manager", "run_id": "r1", "content": "STREAM_ERROR - chat error"},
        ]
        self.deliveries = [self.row("z-source", "r1", "failed")]
        self.sent = []
        self.error = None
        self.after_read = None
        self.query_calls = []
        self.history_calls = []
        self.older_messages = []

    @staticmethod
    def row(message, run, status):
        return {"message_id": message, "delivery_id": "d-" + run, "run_id": run,
                "target_bot_id": "manager", "kind": "send", "status": status}

    async def get_group(self, group_id):
        return {"group_strategy": "manager_worker", "driver_bot": "manager", "status": "active"}

    async def get_session_messages(self, session_id, *, limit, caller_bearer, include_pending, before=None):
        assert session_id == "s1" and limit == 100
        self.history_calls.append(before)
        if before is not None:
            assert not include_pending
            return self.older_messages
        assert include_pending
        assert caller_bearer == "test"
        return list(reversed(self.messages))

    async def query_message_deliveries(self, session_id, *, message_ids, caller_bearer):
        self.query_calls.append((session_id, message_ids, caller_bearer))
        if self.error:
            raise self.error
        if self.after_read:
            self.after_read()
        return [dict(row) for row in self.deliveries if row["message_id"] in message_ids]

    async def send_session_chat(self, session_id, **kwargs):
        self.sent.append((session_id, kwargs))
        return {"delivered": False, "driver_bot": "manager", "queue_admission": {
            "message_id": "resume-source", "deliveries": [self.row("resume-source", "r2", "queued")]}}


@pytest.fixture
def world():
    graph_service = TaskGraphService()
    spec = TaskSpec(context=Context(background="bg", title="test"),
                    goal=Goal(objective="test", acceptances=[AcceptanceCriteria(id="a", description="test")]))
    info = TaskInfo(task_id="t1", task_spec=spec, source_type="bot", owner_bot_id="owner")
    graph = graph_service.initialize_graph(info)
    graph.extend_props["execution_config"]["orchestration_mode"] = "relay"
    child = TaskNode(task_id="t1", node_id="n1", status=Status.PENDING, task_spec=spec,
                     run_info=RuntimeInfo(), node_run_graph=None)
    graph_service.add_task_nodes([child], parent_node_id="t1")
    graph_service.update_task_node_info(TaskNodePatch(
        task_id="t1", node_id="n1", status=Status.RUNNING, run_mode="coop_group", assignee="g1",
        extend_props_patch={"group_id": "g1", "session_id": "s1"},
    ))
    node = graph_service._get_node(graph, "n1")
    bcs = Bcs()
    tokens = SimpleNamespace(get_token=lambda bot: "test")
    clock = SimpleNamespace(now=100.0)
    probe = TaskRuntimeLivenessProbe(bcs=bcs, bot_tokens=tokens)
    recovery = RelaySessionRecovery(bcs=bcs, bot_tokens=tokens)
    harness = TaskHarness(graph_service, clock=lambda: clock.now, runtime_probe=probe, relay_recovery=recovery)
    return SimpleNamespace(graph=graph, service=graph_service, node=node, bcs=bcs,
                           clock=clock, probe=probe, harness=harness)


async def observe(world, advance=120):
    world.clock.now += advance
    return await world.harness._relay_runtime_harness("t1", world.node, elapsed=300, sla=900)


@pytest.mark.asyncio
async def test_two_rounds_resume_once_and_follow_admitted_run(world):
    await observe(world)
    assert not world.bcs.sent
    await observe(world, 1)  # another call in the same interval is not a round
    assert not world.bcs.sent
    await observe(world)
    assert len(world.bcs.sent) == 1
    assert world.bcs.sent[0][1]["caller_bearer"] == "test"
    props = world.node.run_info.extend_props
    assert props["relay_harness_resume_attempts"] == 1
    assert props["relay_harness_resume_run_id"] == "r2"
    assert props["relay_harness_resume_message_id"] == "resume-source"
    world.bcs.messages.append({"id": "resume-source", "timestamp": 3000, "message_type": "chat", "content": "resume"})
    world.bcs.deliveries.append(Bcs.row("resume-source", "r2", "queued"))
    for _ in range(8):
        await observe(world)
    assert len(world.bcs.sent) == 1
    assert world.node.status == Status.RUNNING


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["denied", "running", "unknown", "different_run", "progress"])
async def test_failure_confirmation_restarts_after_interruption(world, interruption):
    await observe(world)
    if interruption == "denied":
        world.bcs.error = PermissionError("403")
    elif interruption == "progress":
        world.bcs.messages.append({"id": "b-tool", "timestamp": 2500, "sender": "manager",
                                   "run_id": "r1", "content": "new tool result"})
    elif interruption == "different_run":
        world.bcs.deliveries[0]["run_id"] = "r-other"
        world.bcs.messages[1]["run_id"] = "r-other"
    else:
        world.bcs.deliveries[0]["status"] = interruption
    await observe(world)
    assert not world.bcs.sent
    world.bcs.error = None
    world.bcs.deliveries[0]["status"] = "failed"
    if interruption not in {"progress", "different_run"}:
        await observe(world)
        assert not world.bcs.sent
    await observe(world)
    assert len(world.bcs.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["closed", "generation", "resume"])
async def test_report_or_new_generation_during_probe_prevents_resume(world, change):
    await observe(world)
    def mutate():
        if change == "closed":
            world.service.update_task_node_info(TaskNodePatch(task_id="t1", node_id="n1", status=Status.HUNG))
        elif change == "generation":
            world.node.run_info.extend_props["relay_token"] = "new-generation"
        else:
            world.node.run_info.extend_props["relay_harness_resume_message_id"] = "another-resume"
    world.bcs.after_read = mutate
    await observe(world)
    assert not world.bcs.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "dispatching", "unknown", "cancelled", "pending_context"])
async def test_new_admission_fences_old_failure(world, status):
    world.bcs.messages.append({"id": "new-input", "timestamp": 3000, "message_type": "chat", "content": "continue"})
    world.bcs.deliveries.append(Bcs.row("new-input", "r2", status))
    for _ in range(8):
        await observe(world)
    assert not world.bcs.sent


@pytest.mark.asyncio
async def test_new_run_without_visible_delivery_does_not_resume_old_run(world):
    world.bcs.messages.append({"id": "new-output", "timestamp": 3000, "sender": "manager",
                               "run_id": "r2", "content": "running"})
    for _ in range(3):
        await observe(world)
    assert not world.bcs.sent


@pytest.mark.asyncio
async def test_unresolved_new_input_and_worker_failure_do_not_authorize_resume(world):
    world.bcs.messages.append({"id": "new-input", "timestamp": 3000, "message_type": "chat", "content": "continue"})
    worker = Bcs.row("new-input", "worker-run", "failed")
    worker["target_bot_id"] = "worker"
    world.bcs.deliveries.append(worker)
    for _ in range(3):
        await observe(world)
    assert not world.bcs.sent


@pytest.mark.asyncio
async def test_error_text_alone_and_trimmed_history_are_not_failure_proof(world):
    world.bcs.messages = world.bcs.messages[1:]
    for _ in range(3):
        await observe(world)
    assert not world.bcs.sent


def test_bcs_timestamp_beats_lexical_uuid():
    assert _last_message([{"id": "z-old", "timestamp": 1000},
                          {"id": "a-new", "timestamp": 2000}])["id"] == "a-new"


@pytest.mark.asyncio
async def test_cached_trimmed_failure_with_unresolved_input_is_not_recovered(world):
    world.node.run_info.extend_props["relay_harness_delivery_message_id"] = "z-source"
    world.bcs.messages = world.bcs.messages[1:] + [
        {"id": "pending", "timestamp": 3000, "message_type": "chat", "content": "continue"}]
    for _ in range(3):
        await observe(world)
    assert not world.bcs.sent


@pytest.mark.asyncio
async def test_cached_admission_is_not_superseded_by_visible_old_failure(world):
    world.node.run_info.extend_props["relay_harness_delivery_message_id"] = "resume-source"
    world.bcs.deliveries.append(Bcs.row("resume-source", "r2", "queued"))
    for _ in range(3):
        await observe(world)
    assert not world.bcs.sent


@pytest.mark.asyncio
async def test_trimmed_history_discovers_and_retains_source(world):
    world.bcs.older_messages = world.bcs.messages[:1]
    world.bcs.messages = [dict(world.bcs.messages[1], id=f"out-{i}", timestamp=2000 + i)
                          for i in range(100)]
    await observe(world)
    assert world.bcs.history_calls == [None, 2000]
    assert world.node.run_info.extend_props["relay_harness_delivery_message_id"] == "z-source"
    await observe(world)
    assert world.bcs.history_calls == [None, 2000, None]
    assert len(world.bcs.sent) == 1


@pytest.mark.asyncio
async def test_failed_resumed_execution_requires_fresh_two_round_confirmation(world):
    await observe(world)
    await observe(world)
    world.bcs.messages.extend([
        {"id": "resume-source", "timestamp": 3000, "message_type": "chat", "content": "resume"},
        {"id": "resume-error", "timestamp": 4000, "sender": "manager", "run_id": "r2",
         "content": "STREAM_ERROR - chat error"}])
    world.bcs.deliveries.append(Bcs.row("resume-source", "r2", "failed"))
    await observe(world, 1000)
    assert len(world.bcs.sent) == 1
    await observe(world)
    assert len(world.bcs.sent) == 2
    assert world.node.run_info.extend_props["relay_harness_resume_attempts"] == 2


@pytest.mark.asyncio
async def test_raw_error_with_running_delivery_does_not_fast_resume(world):
    world.bcs.deliveries[0]["status"] = "running"
    await observe(world)
    await observe(world)
    assert not world.bcs.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["rejected", "wrong_source", "missing_driver"])
async def test_invalid_queue_admission_is_not_counted_as_success(world, invalid):
    async def send(*args, **kwargs):
        row = Bcs.row("resume-source", "r2", "queued")
        result = {"delivered": False, "driver_bot": "manager", "queue_admission": {
            "message_id": "resume-source", "deliveries": [row]}}
        if invalid == "rejected":
            row["status"] = "rejected_capacity"
        elif invalid == "wrong_source":
            row["message_id"] = "different-source"
        else:
            result.pop("driver_bot")
        return result
    world.bcs.send_session_chat = send
    await observe(world)
    await observe(world)
    assert not world.node.run_info.extend_props.get("relay_harness_resume_attempts")


@pytest.mark.asyncio
async def test_unreadable_deliveries_never_become_silence_failure(world):
    world.bcs.error = PermissionError("403")
    result = await world.probe.probe(world.node)
    assert result.state == "activity_unknown"
    assert result.source == "bcs.delivery"
    for _ in range(12):
        await observe(world)
    assert not world.bcs.sent
    assert world.node.status == Status.RUNNING


@pytest.mark.asyncio
async def test_history_search_is_bounded_when_source_is_not_retained(world):
    calls = []

    async def history(session_id, *, limit, caller_bearer, include_pending, before=None):
        calls.append(before)
        end = before or 100000
        return [{"id": f"output-{end-i}", "timestamp": end-i, "sender": "manager",
                 "run_id": "r1", "content": "output"} for i in range(1, 101)]

    world.bcs.get_session_messages = history
    result = await world.probe.probe(world.node)
    assert len(calls) == 8
    assert result.state == "activity_unknown"
    assert not world.bcs.sent


@pytest.mark.asyncio
async def test_completed_delivery_does_not_complete_task_without_report(world):
    world.bcs.deliveries[0]["status"] = "completed"
    await observe(world)
    assert world.node.status == Status.RUNNING
    assert not world.bcs.sent
