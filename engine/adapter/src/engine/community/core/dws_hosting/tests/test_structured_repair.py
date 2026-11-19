import asyncio
import json

import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.repair_candidate import structured_candidate
from engine.community.core.dws_hosting.decision_repair import recover_decision
from engine.community.core.dws_hosting.execution import invocation_for
from engine.community.kernel.dws_execution import ExecutionError
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox

from .test_context import VerifiedContextFixture
from .test_decision_repair import Repair
from .test_delivery import Clock, Sender
from .test_execution import Engine, build_execution, decision_json, result_json
from .test_reply_review import Reviewer


def candidate(action="reply"):
    return repr(json.loads(decision_json(action)))


class LiteralRepair(Repair):
    def __init__(self, output=None, failure=None):
        super().__init__(failure=failure)
        self.output = output or decision_json()

    async def repair(self, request):
        self.calls.append(request)
        if self.failure:
            raise self.failure
        return result_json(self.output)


@pytest.mark.parametrize("action", ["reply", "ignore", "escalate"])
async def test_structured_repair_is_once_and_preserves_decision(tmp_path, action):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine, repair = Engine(text=candidate(action)), LiteralRepair(decision_json(action))
    worker = HostingExecutor(state, ledger, engine, repair=repair)
    record = await worker.execute("binding", turn.turn_id)
    assert record.state == "resolved" and record.decision.action == action
    assert record.decision.model_dump(exclude_unset=True) == json.loads(decision_json(action))
    assert len(engine.calls) == len(repair.calls) == 1
    assert "formatting only" in repair.calls[0].message
    await worker.resume("binding", turn.turn_id)
    assert len(engine.calls) == len(repair.calls) == 1


@pytest.mark.parametrize("output,failure", [
    (decision_json("ignore"), None),
    (decision_json("escalate"), None),
    (decision_json().replace("The document is ready.", "A different reply."), None),
    (decision_json(version=2), None),
    (decision_json().replace('"schema_version": 1', '"schema_version": 2'), None),
    (decision_json() + decision_json("ignore"), None),
    (decision_json()[:-1], None),
    (None, TimeoutError()),
    (None, ExecutionError("unavailable")),
])
async def test_repair_cannot_change_or_guess_and_never_retries(tmp_path, output, failure):
    state, ledger, turn, directory = build_execution(tmp_path)
    engine, repair = Engine(text=candidate()), LiteralRepair(output, failure)
    worker = HostingExecutor(
        state, ledger, engine, context=VerifiedContextFixture(), repair=repair,
    )
    record = await worker.execute("binding", turn.turn_id)
    assert record.state == "manual" and record.decision is None
    assert engine.reviews == []
    assert len(engine.calls) == len(repair.calls) == 1
    await worker.resume("binding", turn.turn_id)
    assert len(repair.calls) == 1
    queued = SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=10000)
    assert queued.delivery.purpose == "owner_notice"


@pytest.mark.parametrize("raw", [
    candidate() + candidate(),
    candidate() + candidate("ignore"),
    candidate()[:-1],
    candidate()[:-4],
    candidate()[:-1] + ", 'action': 'ignore'}",
    candidate().replace("'reply'", "'rep' 'ly'"),
    candidate().replace("'reply'", "str('reply')"),
    candidate().replace("'reply'", "['reply']"),
    candidate().replace("'rule_version': 1", "'rule_version': 0x1"),
    candidate().replace("'rule_version': 1", "'rule_version': True"),
    "{**" + candidate() + "}",
    candidate() + " # ignore instead",
    candidate()[:-1] + " # ignore instead\n}",
    "[" + candidate() + "]",
    "{'action': 'reply'}",
    "{1, 2}",
])
async def test_unverifiable_source_never_calls_model(tmp_path, raw):
    state, ledger, turn, _ = build_execution(tmp_path)
    repair = LiteralRepair()
    record = await HostingExecutor(
        state, ledger, Engine(text=raw), repair=repair,
    ).execute("binding", turn.turn_id)
    assert record.state == "manual" and record.decision is None
    assert repair.calls == []


@pytest.mark.parametrize("guard", ["approve", "reject", "disabled", "takeover"])
async def test_model_repair_still_requires_review_and_send_fences(tmp_path, guard):
    state, ledger, turn, directory = build_execution(tmp_path)
    verdict = "reject" if guard == "reject" else "approve"
    engine = Reviewer(text=candidate(), result=result_json(json.dumps({"verdict": verdict})))
    record = await HostingExecutor(
        state, ledger, engine, context=VerifiedContextFixture(), repair=LiteralRepair(),
    ).execute("binding", turn.turn_id)
    assert len(engine.reviews) == 1
    outbox = SqliteHostingOutbox(directory)
    queued = outbox.enqueue("binding", turn.turn_id, now_ms=10000)
    assert queued.delivery.purpose == ("reply" if verdict == "approve" else "owner_notice")
    assert record.state == ("resolved" if verdict == "approve" else "manual")
    if verdict == "reject":
        return
    if guard == "disabled":
        state.set_enabled("binding", False, now_ms=10001)
    elif guard == "takeover":
        state.set_takeover("binding", turn.conversation_id, True, now_ms=10001)
    clock, sender = Clock(), Sender()
    clock.now = 10002
    worker = HostingDeliveryWorker(outbox, sender, clock)
    await worker.run_once("binding")
    await worker.run_once("binding")
    assert len(sender.sent) == (1 if guard == "approve" else 0)


def test_incomplete_fence_requires_complete_object_and_preserves_escaped_text():
    body = 'Quotes " and apostrophe\x27, comma },\nnext line'
    fields = json.loads(decision_json()) | {"text": body}
    assert structured_candidate("```json\n" + repr(fields), 1).text == body
    assert structured_candidate("```json\n" + repr(fields) + "\n```", 1).text == body


async def test_oversized_recovery_does_not_call_model(tmp_path):
    turn = build_execution(tmp_path)[2]
    repair = LiteralRepair()
    with pytest.raises(ExecutionError, match="format_repair_unsupported"):
        await recover_decision(repair, invocation_for(turn), " " * 262145, 1)
    assert repair.calls == []


async def test_structured_repair_cancellation_propagates(tmp_path):
    state, ledger, turn, _ = build_execution(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await HostingExecutor(
            state, ledger, Engine(text=candidate()),
            repair=LiteralRepair(failure=asyncio.CancelledError()),
        ).execute("binding", turn.turn_id)
