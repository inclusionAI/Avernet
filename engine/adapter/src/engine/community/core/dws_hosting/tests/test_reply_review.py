"""Semantic review is an independent veto, never permission to rewrite or send."""

import asyncio
import json

import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor, invocation_for
from engine.community.core.dws_hosting.reply_review import review_reply
from engine.community.kernel.dws_execution import ExecutionError, HostingDecision
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from .test_execution import Engine, build_execution, decision_json, result_json


def candidate(text="Hi!"):
    return HostingDecision(schema_version=1, rule_version=1, action="reply", text=text)


class Reviewer(Engine):
    def __init__(self, *, result=None, failure=None, text=None):
        super().__init__(text=text)
        self.review_result = result
        self.review_failure = failure

    async def review_reply(self, request):
        self.reviews.append(request)
        if self.review_failure:
            raise self.review_failure
        return self.review_result


def hi_request(tmp_path):
    return invocation_for(build_execution(tmp_path, text="hi")[2])


async def test_reviewer_sees_current_batch_and_rules_not_old_conversation(tmp_path):
    request = hi_request(tmp_path)
    engine = Engine()
    await review_reply(engine, request, candidate())
    check = engine.reviews[0]
    data = json.loads(check.message.split("\nINPUT_JSON:\n", 1)[1])
    assert data["dialogue"][0]["text"] == "hi"
    assert data["candidate_reply"] == "Hi!"
    assert data["confirmed_rules"] == json.loads(
        request.instructions.split("\nConfirmed rule snapshot:\n")[1]
    )
    assert "history" not in data and "messages" not in data
    assert check.session_key != request.session_key
    assert check.run_id == request.run_id + "-review"
    assert check.rpc_id != request.rpc_id
    assert "greeting" in check.message
    assert engine.calls == []


async def test_off_topic_greeting_reply_is_not_sendable_after_review_veto(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path, text="hi")
    wrong = candidate("Here are birthday card ideas for your grandmother.")
    engine = Reviewer(
        text=wrong.model_dump_json(exclude_unset=True),
        result=result_json('{"verdict":"reject"}'),
    )
    worker = HostingExecutor(state, ledger, engine)
    record = await worker.execute("binding", turn.turn_id)
    assert record.state == "manual"
    assert record.reason == "reply_review_rejected"
    assert record.decision is None
    assert record.result is not None
    assert len(engine.calls) == len(engine.reviews) == 1
    await worker.resume("binding", turn.turn_id)
    assert len(engine.calls) == len(engine.reviews) == 1
    delivery = SqliteHostingOutbox(directory).enqueue(
        "binding", turn.turn_id, now_ms=7000
    )
    assert delivery.delivery.purpose == "owner_notice"
    assert wrong.text not in delivery.delivery.text


@pytest.mark.parametrize(
    "result,reason",
    [
        (result_json('{"verdict":"skip_owner_replied"}'), "reply_review_rejected"),
        (result_json('{"verdict":"reject"}'), "reply_review_rejected"),
        (
            result_json('{"verdict":"approve","text":"rewritten"}'),
            "reply_review_unavailable",
        ),
        (
            result_json('{"verdict":"approve","verdict":"reject"}'),
            "reply_review_unavailable",
        ),
        (result_json('{"verdict":"unknown"}'), "reply_review_unavailable"),
        (result_json('{"verdict":null}'), "reply_review_unavailable"),
        (result_json(decision_json(version=999)), "reply_review_unavailable"),
        (result_json("Looks good"), "reply_review_unavailable"),
        ('{"payloads":[]}', "reply_review_unavailable"),
        ('{"payloads":[{"text":"{}"},{"text":"{}"}]}', "reply_review_unavailable"),
        (
            '{"payloads":[{"text":"{}"}],"meta":{"aborted":true}}',
            "reply_review_unavailable",
        ),
        ('{"payloads":[{"text":"{}","isError":true}]}', "reply_review_unavailable"),
    ],
)
async def test_uncertain_invalid_or_rewritten_review_never_approves(
    tmp_path, result, reason
):
    engine = Reviewer(result=result)
    with pytest.raises(ExecutionError, match=reason):
        await review_reply(engine, hi_request(tmp_path), candidate())
    assert len(engine.reviews) == 1


@pytest.mark.parametrize(
    "failure,reason",
    [
        (ExecutionError("private transport body"), "reply_review_unavailable"),
        (TimeoutError("private transport body"), "reply_review_timeout"),
    ],
)
async def test_review_failure_is_manual_without_replaying_original(
    tmp_path, failure, reason
):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine = Reviewer(failure=failure)
    record = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert record.state == "manual" and record.reason == reason
    assert record.decision is None
    assert len(engine.calls) == len(engine.reviews) == 1


@pytest.mark.parametrize("action", ["ignore", "escalate"])
async def test_non_reply_never_invokes_reviewer(tmp_path, action):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine = Reviewer(text=decision_json(action), failure=AssertionError())
    record = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert record.decision.action == action
    assert engine.reviews == []


async def test_cancelled_review_propagates_without_resolving(tmp_path):
    state, ledger, turn, _ = build_execution(tmp_path)
    engine = Reviewer(failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await HostingExecutor(state, ledger, engine).execute("binding", turn.turn_id)
    assert ledger.get("binding", turn.turn_id).decision is None
    assert len(engine.calls) == 1


@pytest.mark.parametrize("message", ["bad-json", "oversize"])
async def test_review_input_is_bounded(tmp_path, message):
    request = hi_request(tmp_path)
    request = request.model_copy(
        update={
            "message": "bad-json"
            if message == "bad-json"
            else json.dumps({"text": "x" * 200_000})
        }
    )
    engine = Engine()
    with pytest.raises(ExecutionError, match="reply_review_input_invalid"):
        await review_reply(engine, request, candidate())
    assert engine.reviews == []


async def test_disable_during_review_still_fences_resolution(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path)

    class DisableWhileReviewing(Engine):
        async def review_reply(self, request):
            state.set_enabled("binding", False, now_ms=7000)
            return await super().review_reply(request)

    engine = DisableWhileReviewing()
    record = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert record.state == "cancelled" and record.reason.startswith("control_revoked")
    assert (
        SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=7000)
        is None
    )
    assert len(engine.calls) == len(engine.reviews) == 1


async def test_owner_already_answered_is_resolved_without_any_delivery(
    tmp_path, monkeypatch
):
    state, ledger, turn, directory = build_execution(tmp_path, text="hi")
    request = invocation_for(turn)
    latest = json.loads(request.message)["current_batch"][0]["created_at_ms"]
    context = ({"role": "owner", "text": "Hello!", "created_at_ms": latest + 1},)
    monkeypatch.setattr(type(ledger), "review_context", lambda *args: context)
    engine = Reviewer(result=result_json('{"verdict":"skip_owner_replied"}'))
    worker = HostingExecutor(state, ledger, engine)
    record = await worker.execute("binding", turn.turn_id)
    assert record.state == "resolved"
    assert record.decision.action == "ignore"
    assert (
        SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=7000)
        is None
    )
    await worker.resume("binding", turn.turn_id)
    assert len(engine.calls) == len(engine.reviews) == 1


@pytest.mark.parametrize(
    "role,offset,truncated",
    [
        ("owner", -1, False),
        ("owner", 0, False),
        ("peer", 1, False),
        ("assistant_sent", 1, False),
        ("owner", 1, True),
    ],
)
async def test_silent_skip_requires_later_complete_owner_message(
    tmp_path, role, offset, truncated
):
    request = hi_request(tmp_path)
    latest = json.loads(request.message)["current_batch"][0]["created_at_ms"]
    context = (
        {
            "role": role,
            "text": "hello",
            "created_at_ms": latest + offset,
            "truncated": truncated,
        },
    )
    engine = Reviewer(result=result_json('{"verdict":"skip_owner_replied"}'))
    with pytest.raises(ExecutionError, match="reply_review_rejected"):
        await review_reply(engine, request, candidate(), context)


async def test_owner_message_does_not_override_review_timeout(tmp_path):
    request = hi_request(tmp_path)
    latest = json.loads(request.message)["current_batch"][0]["created_at_ms"]
    context = ({"role": "owner", "text": "unrelated", "created_at_ms": latest + 1},)
    engine = Reviewer(failure=TimeoutError())
    with pytest.raises(ExecutionError, match="reply_review_timeout"):
        await review_reply(engine, request, candidate(), context)


async def test_compact_input_preserves_all_dialogue_rules_and_facts(tmp_path):
    request = hi_request(tmp_path)
    original = json.loads(request.message)
    current = original["current_batch"][0]
    history = tuple(
        {
            "key": f"message:{i}",
            "role": "owner" if i % 2 else "peer",
            "text": "same text",
            "created_at_ms": i,
            "truncated": False,
        }
        for i in range(40)
    )
    history += (
        {
            "key": "message:" + current["message_id"],
            "role": "peer",
            "text": current["text"],
            "created_at_ms": current["created_at_ms"],
        },
    )
    facts = {
        "owner": {"is_manager": "unknown"},
        "sender": {"is_hr": "unknown"},
        "binding_id": "internal",
        "turn_id": "internal",
    }
    original.update(
        directory_context=facts,
        conversation_updates=list(history),
        delivery_updates=[{"run_id": "internal"}],
        context_window={"limited": True},
    )
    request = request.model_copy(update={"message": json.dumps(original)})
    engine = Engine()
    proposal = candidate("Keep this exact text.")
    assert await review_reply(engine, request, proposal, history) is proposal
    data = json.loads(engine.reviews[0].message.split("\nINPUT_JSON:\n")[1])
    assert len(data["dialogue"]) == 41
    assert sum(m.get("current_batch", False) for m in data["dialogue"]) == 1
    assert sum(m["text"] == "same text" for m in data["dialogue"]) == 40
    assert data["directory_facts"] == {
        "owner": facts["owner"],
        "sender": facts["sender"],
    }
    assert data["context_window"] == {"limited": True}
    assert (
        not {
            "original_input",
            "original_instructions",
            "conversation_updates",
            "delivery_updates",
            "adapter_state",
        }
        & data.keys()
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"verdict":"approve"}{"verdict":"reject"}',
        'Looks good: {"verdict":"approve"}',
        '{"verdict":"approve"} trailing',
    ],
)
async def test_ambiguous_verdict_is_never_sendable(tmp_path, raw):
    with pytest.raises(ExecutionError, match="reply_review_unavailable"):
        await review_reply(
            Reviewer(result=result_json(raw)), hi_request(tmp_path), candidate()
        )
