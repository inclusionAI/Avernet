import sqlite3
import json

import pytest

from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.dialogue import HostingDialogue
from engine.community.core.dws_hosting.owner_activity import HostingOwnerActivity
from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.kernel.dws_connection import DwsError, HistoryResult
from engine.community.kernel.dws_dialogue import DialogueMessage, DialogueSnapshot
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from .test_delivery import case as delivery_case, enqueue
from .test_dialogue import History
from .test_execution import Engine, build_execution
from .test_context import VerifiedContextFixture
from .test_state_contract import message
from .test_connection_wire import history_payload, row
from engine.community.plugins.dws_hosting import wire
from engine.community.kernel.dws_connection import ConnectionIdentity

case = delivery_case


def guarded(case, source):
    state, ledger, _, _, outbox, clock, sender = case
    activity = HostingOwnerActivity(
        state, ledger, outbox, HostingDialogue(source, clock)
    )
    return HostingDeliveryWorker(outbox, sender, clock, owner_activity=activity)


async def test_owner_reply_after_generation_skips_review_and_notification(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path)
    source = History(turn.messages)
    dialogue = HostingDialogue(source, lambda: 9000)
    outbox = SqliteHostingOutbox(directory)
    activity = HostingOwnerActivity(state, ledger, outbox, dialogue)
    engine = Engine()
    engine.on_ack = lambda: setattr(
        source,
        "messages",
        (
            *turn.messages,
            message("human", sender="self", created=8000, text="我来处理了"),
        ),
    )
    executor = HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=dialogue,
        owner_activity=activity,
    )
    record = await executor.execute("binding", turn.turn_id)
    assert record.state == "resolved" and record.decision.action == "ignore"
    assert not engine.reviews
    assert outbox.enqueue("binding", turn.turn_id, now_ms=10000) is None


@pytest.mark.parametrize("purpose", ["reply", "owner_notice"])
async def test_new_owner_reply_at_last_preflight_silently_cancels(case, purpose):
    row = enqueue(case)
    if purpose == "owner_notice":
        delivery = row.delivery.model_copy(update={"purpose": purpose})
        with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
            db.execute(
                "UPDATE outbox SET delivery_json=?", (delivery.model_dump_json(),)
            )
    source = History()
    case[6].on_preflight = lambda: setattr(
        source, "messages", (message("manual", sender="self", created=9000),)
    )
    result = await guarded(case, source).run_once("binding")
    assert result.state == "cancelled" and result.reason == "owner_replied"
    assert not case[6].sent
    assert await guarded(case, source).run_once("binding") is None
    with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


@pytest.mark.parametrize(
    "kind", ["foreign", "sender", "unavailable", "timeout", "unsupported"]
)
async def test_untrusted_or_missing_fresh_history_never_sends(case, kind):
    enqueue(case)
    source = History((message("x", chat="foreign", sender="self", created=8000),))
    if kind == "sender":
        source.messages = (message("x", sender="foreign", created=8000),)
    elif kind == "unsupported":

        async def incomplete(*args):
            return HistoryResult(messages=(), unsupported_count=1)

        source.history = incomplete
    elif kind != "foreign":
        source.error = (
            TimeoutError() if kind == "timeout" else DwsError("incomplete_history")
        )
    assert (await guarded(case, source).run_once("binding")).state == "queued"
    assert not case[6].sent


async def test_already_seen_owner_context_does_not_always_cancel(case):
    row = enqueue(case)
    old = message("seen-owner", sender="self", created=2000)
    invocation = case[1].get("binding", case[2].turn_id).invocation
    invocation = invocation.model_copy(
        update={
            "dialogue_snapshot": DialogueSnapshot(
                captured_at_ms=3000,
                messages=(
                    DialogueMessage(
                        key="message:seen-owner",
                        role="owner",
                        text=old.text,
                        created_at_ms=old.created_at_ms,
                    ),
                ),
            )
        }
    )
    with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
        db.execute(
            "UPDATE executions SET invocation_json=? WHERE turn_id=?",
            (invocation.model_dump_json(), case[2].turn_id),
        )
    result = await guarded(case, History((old,))).run_once("binding")
    assert result.state == "sent" and case[6].sent == [row.delivery]


async def test_recorded_ai_reply_is_not_human_activity(case):
    enqueue(case)
    with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
        db.execute("UPDATE outbox SET state='sent', message_id='automatic'")
    activity = guarded(
        case, History((message("automatic", sender="self", created=9000),))
    ).owner_activity
    assert not await activity.superseded("binding", case[2].turn_id)
    with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
        db.execute("UPDATE outbox SET state='queued'")
    assert await activity.superseded("binding", case[2].turn_id)


async def test_disable_during_guard_still_fences_send(case):
    enqueue(case)

    class Disable(History):
        async def history(self, *args):
            case[0].set_enabled("binding", False, now_ms=case[5]())
            return await super().history(*args)

    result = await guarded(case, Disable()).run_once("binding")
    assert result.state == "cancelled" and not case[6].sent


@pytest.mark.parametrize(
    "kind", ["ai", "peer_media", "owner_media", "owner_text_and_ai"]
)
async def test_canonical_activity_is_independent_of_renderable_text(case, kind):
    queued = enqueue(case)
    rows = [
        row(
            messageId="auto",
            senderId="open-self",
            messageAiSendFlag="DWS",
            createTime=8_000_000_000_000,
        )
    ]
    if kind in ("peer_media", "owner_media"):
        rows.append(
            row(
                messageId="image",
                senderId="open-self" if kind == "owner_media" else "peer",
                messageType="image",
                createTime=8_000_000_000_000,
            )
        )
    if kind == "owner_text_and_ai":
        rows.append(
            row(messageId="manual", senderId="open-self", createTime=8_000_000_000_000)
        )
    # Use canonical wire parsing, then map fixture time back to this test's clock.
    parsed = wire.history(
        json.dumps(history_payload(rows)),
        "chat",
        8_000_000_000_000,
        8_000_000_001_000,
        ConnectionIdentity(
            organization_id="org", user_id="self", self_open_id="open-self"
        ),
        "UTC",
    )
    source = History()

    async def history(*args):
        return parsed.model_copy(
            update={
                "messages": tuple(
                    m.model_copy(update={"created_at_ms": 9000})
                    for m in parsed.messages
                ),
                "activities": tuple(
                    m.model_copy(update={"created_at_ms": 9000})
                    for m in parsed.activities
                ),
            }
        )

    source.history = history
    result = await guarded(case, source).run_once("binding")
    if kind.startswith("owner"):
        assert result.state == "cancelled" and not case[6].sent
    else:
        assert result.state == "sent" and case[6].sent == [queued.delivery]


def test_stale_skip_cannot_cancel_another_claim_or_inflight_write(case):
    enqueue(case)
    outbox = case[4]
    first = outbox.claim("binding", now_ms=10000)
    second = outbox.claim("binding", now_ms=first.lease_until_ms)
    outbox.skip(first, now_ms=first.lease_until_ms)
    assert outbox.get("binding", first.delivery.delivery_id).state == "claimed"
    sending = outbox.start_send(second, now_ms=first.lease_until_ms)
    outbox.skip(second, now_ms=first.lease_until_ms)
    assert outbox.get("binding", first.delivery.delivery_id).state == "sending"
    assert sending is not None
