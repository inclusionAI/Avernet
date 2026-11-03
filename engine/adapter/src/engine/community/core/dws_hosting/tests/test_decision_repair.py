import asyncio
import json

import pytest

from engine.community.core.dws_hosting.decision_repair import recover_decision
from engine.community.core.dws_hosting.execution import HostingExecutor, invocation_for
from engine.community.kernel.dws_execution import ExecutionError
from .test_execution import Engine, build_execution, result_json


class Repair:
    def __init__(self, action="reply", text="A plain reply.", version=1, failure=None):
        self.calls = []
        self.action, self.text, self.version, self.failure = action, text, version, failure

    async def repair(self, request):
        self.calls.append(request)
        if self.failure:
            raise self.failure
        decision = {"schema_version": 1, "rule_version": self.version, "action": self.action}
        if self.action == "reply":
            decision["text"] = self.text
        if self.action == "escalate":
            decision["summary"] = "Uncertain"
        return result_json(json.dumps(decision))


@pytest.mark.parametrize("action", ["reply", "ignore", "escalate"])
async def test_plain_result_recovered_without_replaying_agent(tmp_path, action):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine, repair = Engine(text="A plain reply."), Repair(action)
    worker = HostingExecutor(state, ledger, engine, repair=repair)
    result = await worker.execute("binding", turn.turn_id)
    assert result.state == "resolved" and result.decision.action == action
    assert len(engine.calls) == len(repair.calls) == 1
    req = repair.calls[0]
    assert req.session_key != engine.calls[0].session_key
    assert req.run_id == engine.calls[0].run_id + "-format"
    assert turn.rule_text in req.message
    assert "candidate_text" in req.message
    await worker.resume("binding", turn.turn_id)
    assert len(repair.calls) == 1


@pytest.mark.parametrize("kwargs", [
    {"text": "Different reply"},
    {"version": 999},
    {"failure": ExecutionError("unavailable")},
    {"failure": TimeoutError()},
])
async def test_bad_repair_stays_manual(tmp_path, kwargs):
    state, ledger, turn, _ = build_execution(tmp_path)
    repair = Repair(**kwargs)
    result = await HostingExecutor(
        state, ledger, Engine(text="A plain reply."), repair=repair,
    ).execute("binding", turn.turn_id)
    assert result.state == "manual"
    assert result.decision is None
    assert len(repair.calls) == 1


@pytest.mark.parametrize("result", [
    {"payloads": [{"text": "one"}, {"text": "two"}]},
    {"payloads": [{"text": "reply", "mediaUrl": "https://example.invalid/image"}]},
    {"payloads": [{"text": "reply", "isError": True}]},
    {"payloads": [{"text": "reply"}], "meta": {"aborted": True}},
    {"payloads": [{"text": '{"action":"reply"}'}]},
    {"payloads": [{"text": " "}]},
    {"payloads": [{"text": "x" * 8001}]},
    {"payloads": [None]},
    {},
])
async def test_ambiguous_or_structured_result_not_reinterpreted(tmp_path, result):
    turn = build_execution(tmp_path)[2]
    repair = Repair()
    with pytest.raises(ExecutionError):
        await recover_decision(repair, invocation_for(turn), json.dumps(result), 1)
    assert repair.calls == []


async def test_repair_cancellation_propagates(tmp_path):
    turn = build_execution(tmp_path)[2]
    repair = Repair(failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await recover_decision(repair, invocation_for(turn), result_json("plain"), 1)


async def test_repair_respects_invocation_bounds(tmp_path):
    request = invocation_for(build_execution(tmp_path)[2])
    request = request.model_copy(update={"instructions": "x" * 40_000})
    repair = Repair()
    with pytest.raises(ExecutionError, match="input_too_large"):
        await recover_decision(repair, request, result_json("plain"), 1)
    assert repair.calls == []
