"""Lossless recovery uses the existing review and durable sending fences."""

import json

import pytest

from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.core.dws_hosting.formatting import repair_format
from engine.community.kernel.dws_execution import EngineUpdate, ExecutionError
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox

from .test_context import VerifiedContextFixture
from .test_decision_repair import Repair
from .test_delivery import Clock, Sender
from .test_execution import Engine, build_execution, decision_json


class EnvelopeEngine(Engine):
    async def execute(self, request):
        self.calls.append(request)
        yield EngineUpdate(
            run_id=request.run_id, status="completed",
            result_json=json.dumps({"payloads": [{"text": self.text, "mediaUrl": None}]}),
        )


@pytest.mark.parametrize("action", ["reply", "ignore", "escalate"])
@pytest.mark.parametrize("suffix", [",", " , \n"])
def test_single_terminal_comma_preserves_decision(action, suffix):
    original = decision_json(action)
    result = repair_format(json.dumps({"payloads": [
        {"text": original + suffix, "mediaUrl": None, "isError": False},
    ]}), 1)
    assert result.model_dump(exclude_unset=True) == json.loads(original)


@pytest.mark.parametrize("payload", [
    {"mediaUrl": "image"}, {"mediaUrls": ["image"]}, {"isError": True},
])
def test_formatting_keeps_payload_safety_markers(payload):
    with pytest.raises(ExecutionError):
        repair_format(json.dumps({"payloads": [
            {"text": decision_json() + ",", **payload},
        ]}), 1)


@pytest.mark.parametrize("raw", [
    json.dumps({"payloads": {"text": "ignored"}}),
    json.dumps({"payloads": [None]}),
    json.dumps({"payloads": [{"text": None}]}),
    json.dumps({"payloads": [{"text": decision_json() + ","}],
                "meta": {"aborted": True}}),
    " " * 262145,
])
def test_invalid_envelope_cannot_be_repaired(raw):
    with pytest.raises(ExecutionError):
        repair_format(raw, 1)


def test_recovery_keeps_escaped_punctuation_and_complete_fences():
    body = 'Literal }, commas, and "quotes"\nsecond line'
    original = json.loads(decision_json()) | {"text": body}
    raw = json.dumps(original)
    result = repair_format(json.dumps({"payloads": [
        {"text": "```json\n" + raw + ",\n```", "mediaUrl": None},
    ]}), 1)
    assert result.text == body


BAD_OUTPUTS = [
    decision_json() + "," + decision_json(),
    decision_json() + decision_json() + ",",
    decision_json() + ",\n" + decision_json("ignore"),
    decision_json() + ",\n" + decision_json("escalate"),
    decision_json()[:-1] + ",",
    decision_json()[:-3] + ",",
    decision_json() + ",,",
    decision_json() + ", trailing",
    decision_json(version=2) + ",",
    decision_json()[:-1] + ', "action":"ignore"},',
    decision_json()[:-1] + ', "recipient":"other"},',
]


@pytest.mark.parametrize("text", BAD_OUTPUTS)
async def test_ambiguous_damaged_output_never_queues_peer_reply(tmp_path, text):
    state, ledger, turn, directory = build_execution(tmp_path)
    engine, repair = EnvelopeEngine(text=text), Repair()
    result = await HostingExecutor(
        state, ledger, engine, context=VerifiedContextFixture(), repair=repair,
    ).execute("binding", turn.turn_id)
    assert result.state == "manual" and result.decision is None
    assert engine.reviews == repair.calls == []
    queued = SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=10000)
    assert queued is not None and queued.delivery.purpose == "owner_notice"


@pytest.mark.parametrize("guard", ["none", "review_reject", "disabled", "takeover", "identical"])
async def test_repaired_reply_cannot_bypass_review_or_send_fences(tmp_path, guard):
    state, ledger, turn, directory = build_execution(tmp_path)

    class Reviewed(EnvelopeEngine):
        async def review_reply(self, request):
            self.reviews.append(request)
            verdict = "reject" if guard == "review_reject" else "approve"
            return json.dumps({"payloads": [{"text": json.dumps({"verdict": verdict})}]})

    text = decision_json() * 2 if guard == "identical" else decision_json() + ","
    engine, repair = Reviewed(text=text), Repair()
    executor = HostingExecutor(
        state, ledger, engine, context=VerifiedContextFixture(), repair=repair,
    )
    result = await executor.execute("binding", turn.turn_id)
    assert len(engine.calls) == len(engine.reviews) == 1
    assert repair.calls == []
    outbox = SqliteHostingOutbox(directory)
    queued = outbox.enqueue("binding", turn.turn_id, now_ms=10000)
    if guard == "review_reject":
        assert result.state == "manual" and result.decision is None
        assert queued.delivery.purpose == "owner_notice"
        return
    assert result.state == "resolved"
    assert queued.delivery.text == json.loads(decision_json())["text"]
    if guard == "disabled":
        state.set_enabled("binding", False, now_ms=10001)
    elif guard == "takeover":
        state.set_takeover("binding", turn.conversation_id, True, now_ms=10001)
    sender = Sender()
    clock = Clock()
    clock.now = 10002
    worker = HostingDeliveryWorker(outbox, sender, clock)
    await worker.run_once("binding")
    await worker.run_once("binding")
    assert len(sender.sent) == (1 if guard in ("none", "identical") else 0)
