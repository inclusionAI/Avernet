"""Real SQLite outbox conformance and worker fault injection; no live sends."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.execution import invocation_for
from engine.community.core.dws_hosting.results import parse_decision
from engine.community.kernel.dws_delivery import (
    AI_LABEL,
    DeliveryPolicy,
    DeliveryReceipt,
    PreflightError,
)
from engine.community.kernel.dws_execution import EngineUpdate
from engine.community.kernel.dws_management import HostingSettings
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore

from .test_execution import build_execution, decision_json, result_json, ready_more


class Clock:
    now = 10000

    def __call__(self):
        return self.now


class Sender:
    def __init__(self):
        self.preflights, self.sent, self.inspected = [], [], []
        self.receipt = DeliveryReceipt(
            status="sent", message_id="outgoing", conversation_id="chat"
        )
        self.on_preflight = lambda: None
        self.on_send = lambda: None

    async def preflight(self, delivery):
        self.preflights.append(delivery)
        self.on_preflight()

    async def send(self, delivery):
        self.sent.append(delivery)
        self.on_send()
        await asyncio.sleep(0)
        return self.receipt

    async def inspect(self, delivery, task_id):
        self.inspected.append((delivery, task_id))
        return self.receipt


def resolve(ledger, turn, *, action="reply"):
    from .test_context import permitted_context
    request = invocation_for(turn, permitted_context(turn))
    ledger.reserve(turn, request)
    raw = result_json(decision_json(action, version=turn.rule_version))
    ledger.record_update(
        "binding",
        turn.turn_id,
        EngineUpdate(run_id=request.run_id, status="completed", result_json=raw),
    )
    ledger.resolve("binding", turn.turn_id, parse_decision(raw, turn.rule_version))


@pytest.fixture
def case(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn)
    return (
        state,
        ledger,
        turn,
        directory,
        SqliteHostingOutbox(directory),
        Clock(),
        Sender(),
    )


def enqueue(case, **kwargs):
    return case[4].enqueue("binding", case[2].turn_id, now_ms=case[5](), **kwargs)


def worker(case):
    return HostingDeliveryWorker(case[4], case[6], case[5])


async def test_reply_body_has_no_label_or_legacy_suffix(case):
    first = enqueue(case, label_suffix="来自我的助手")
    assert not first.delivery.text.startswith(AI_LABEL)
    assert "来自我的助手" not in first.delivery.text
    assert enqueue(case, label_suffix="changed") == first
    result = await worker(case).run_once("binding")
    assert result.state == "sent" and result.message_id == "outgoing"
    assert len(case[6].sent) == 1
    assert await worker(case).run_once("binding") is None
    assert ready_more(case[0]) is not None


async def test_legacy_saved_suffix_does_not_enter_reply_body(case):
    state, ledger = case[:2]
    enqueue(case)
    await worker(case).run_once("binding")
    state.confirm_rule(
        "binding", HostingSettings(rules="work", label_suffix="legacy suffix").instructions()
    )
    turn = ready_more(state)
    resolve(ledger, turn)
    state.confirm_rule(
        "binding", HostingSettings(rules="new", label_suffix="new suffix").instructions()
    )
    record = case[4].enqueue("binding", turn.turn_id, now_ms=12000)
    assert not record.delivery.text.startswith(AI_LABEL)
    assert "suffix" not in record.delivery.text
    assert case[4].get("binding", record.delivery.delivery_id) == record
    assert case[4].enqueue("binding", turn.turn_id, now_ms=13000) == record


async def test_task_id_is_not_success_and_only_queries_are_retried(case):
    enqueue(case)
    sender, clock = case[6], case[5]
    sender.receipt = DeliveryReceipt(status="accepted", task_id="task")
    result = await worker(case).run_once("binding")
    assert result.state == "awaiting" and result.message_id == ""
    assert ready_more(case[0]) is None
    assert await worker(case).run_once("binding") is None
    clock.now = result.due_ms
    assert (await worker(case).run_once("binding")).state == "awaiting"
    clock.now += 5000
    sender.receipt = DeliveryReceipt(
        status="sent", task_id="task", message_id="real", conversation_id="chat"
    )
    result = await worker(case).run_once("binding")
    assert result.state == "sent" and result.message_id == "real"
    assert len(sender.sent) == 1 and len(sender.inspected) == 2
    assert case[0].claim_ready("binding", now_ms=11000) is not None


async def test_concurrent_workers_send_once(case):
    enqueue(case)
    results = await asyncio.gather(
        *(worker(case).run_once("binding") for _ in range(10))
    )
    assert sum(result is not None for result in results) == 1
    assert len(case[6].sent) == 1


def test_enqueue_is_atomic_and_same_delivery_key_after_restart(case):
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda _: enqueue(case), range(8)))
    assert len({r.delivery.delivery_id for r in records}) == 1
    restarted = SqliteHostingOutbox(case[3])
    assert restarted.enqueue("binding", case[2].turn_id, now_ms=12000) == records[0]


@pytest.mark.parametrize("control", ["disable", "takeover"])
@pytest.mark.parametrize("moment", ["enqueue", "claim", "preflight"])
async def test_revocation_prevents_unstarted_writes(case, control, moment):
    state, _, _, _, outbox, clock, sender = case

    def revoke():
        if control == "disable":
            state.set_enabled("binding", False, now_ms=clock())
            state.set_enabled("binding", True, now_ms=clock() + 1)
        else:
            state.set_takeover("binding", "chat", True, now_ms=clock())
            state.set_takeover("binding", "chat", False, now_ms=clock() + 1)

    if moment == "enqueue":
        revoke()
    record = enqueue(case)
    if moment == "claim":
        revoke()
    elif moment == "preflight":
        sender.on_preflight = revoke
    await worker(case).run_once("binding")
    assert outbox.get("binding", record.delivery.delivery_id).state == "cancelled"
    assert sender.sent == []


async def test_rule_update_does_not_cancel_frozen_reply(case):
    record = enqueue(case)
    case[0].confirm_rule("binding", "Do not reply in future turns.")
    assert (await worker(case).run_once("binding")).state == "sent"
    assert case[6].sent[0] == record.delivery


async def test_control_after_dispatch_does_not_lie_about_inflight_delivery(case):
    enqueue(case)
    sender, clock = case[6], case[5]
    sender.on_send = lambda: case[0].set_enabled("binding", False, now_ms=clock())
    sender.receipt = DeliveryReceipt(status="accepted", task_id="task")
    result = await worker(case).run_once("binding")
    assert result.state == "awaiting"
    clock.now = result.due_ms
    sender.receipt = DeliveryReceipt(
        status="sent", task_id="task", message_id="out", conversation_id="chat"
    )
    assert (await worker(case).run_once("binding")).state == "sent"
    assert len(sender.sent) == 1


async def test_preflight_retries_bounded_with_persisted_backoff(case):
    enqueue(case)
    sender, clock = case[6], case[5]

    def unavailable():
        raise PreflightError("unavailable", retryable=True)

    sender.on_preflight = unavailable
    for delay in (2000, 5000, 15000):
        before = clock()
        result = await worker(case).run_once("binding")
        assert (
            result.state == "queued"
            and before + delay <= result.due_ms < before + delay * 1.2
        )
        assert await worker(case).run_once("binding") is None
        assert (
            SqliteHostingOutbox(case[3])
            .get("binding", result.delivery.delivery_id)
            .due_ms
            == result.due_ms
        )
        clock.now = result.due_ms
    assert (await worker(case).run_once("binding")).state == "manual"
    assert len(sender.preflights) == 4 and sender.sent == []
    assert ready_more(case[0]) is not None


async def test_invalid_authorization_is_not_retried(case):
    enqueue(case)

    def logged_out():
        raise PreflightError("authorization_required")

    case[6].on_preflight = logged_out
    assert (await worker(case).run_once("binding")).state == "manual"
    assert case[6].sent == []


@pytest.mark.parametrize(
    "failure", [TimeoutError, RuntimeError, asyncio.CancelledError]
)
async def test_send_exception_never_causes_resend(case, failure):
    row = enqueue(case)

    def fail():
        raise failure()

    case[6].on_send = fail
    if failure is asyncio.CancelledError:
        with pytest.raises(failure):
            await worker(case).run_once("binding")
    else:
        assert (await worker(case).run_once("binding")).state == "unknown"
    case[5].now += 1000000
    assert await worker(case).run_once("binding") is None
    assert case[4].get("binding", row.delivery.delivery_id).state == "unknown"
    assert len(case[6].sent) == 1
    assert ready_more(case[0]) is None
    assert ready_more(case[0], "other") is not None


def test_expired_claim_reclaimed_but_expired_write_never_replayed(case):
    enqueue(case)
    outbox, clock = case[4:6]
    first = outbox.claim("binding", now_ms=clock())
    clock.now = first.lease_until_ms
    second = outbox.claim("binding", now_ms=clock())
    assert second.claim_id != first.claim_id
    assert outbox.start_send(first, now_ms=clock()) is None
    outbox.preflight_failed(first, now_ms=clock(), retryable=False)
    sending = outbox.start_send(second, now_ms=clock())
    clock.now = sending.lease_until_ms
    assert SqliteHostingOutbox(case[3]).claim("binding", now_ms=clock()) is None
    assert outbox.get("binding", sending.delivery.delivery_id).state == "unknown"
    # A real, late ACK from the original write can still supply its task for inspection.
    outbox.complete(
        sending, DeliveryReceipt(status="accepted", task_id="late"), now_ms=clock()
    )
    assert outbox.get("binding", sending.delivery.delivery_id).state == "awaiting"


async def test_poll_budget_retains_unknown_no_history_guess_or_resend(case):
    enqueue(case)
    outbox = SqliteHostingOutbox(case[3], DeliveryPolicy(max_polls=2))
    sender, clock = case[6], case[5]
    sender.receipt = DeliveryReceipt(status="accepted", task_id="task")
    job = HostingDeliveryWorker(outbox, sender, clock)
    result = await job.run_once("binding")
    for _ in range(2):
        clock.now = result.due_ms
        result = await job.run_once("binding")
    assert result.state == "unknown"
    assert len(sender.sent) == 1 and len(sender.inspected) == 2
    clock.now += 100000
    assert await job.run_once("binding") is None


@pytest.mark.parametrize("mismatch", ["task", "conversation"])
async def test_mismatched_receipt_cannot_confirm_delivery(case, mismatch):
    enqueue(case)
    sender, clock = case[6], case[5]
    sender.receipt = DeliveryReceipt(status="accepted", task_id="task")
    pending = await worker(case).run_once("binding")
    clock.now = pending.due_ms
    sender.receipt = DeliveryReceipt(
        status="sent",
        task_id="wrong" if mismatch == "task" else "task",
        message_id="other",
        conversation_id="wrong" if mismatch == "conversation" else "chat",
    )
    result = await worker(case).run_once("binding")
    assert result.state == "unknown" and result.reason == "receipt_scope_mismatch"
    assert result.message_id == "" and result.task_id == "task"


async def test_write_receipt_db_failure_is_never_retried(case):
    row = enqueue(case)
    with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
        db.execute("""CREATE TRIGGER deny_sent BEFORE UPDATE ON outbox
                      WHEN NEW.state = 'sent' BEGIN SELECT RAISE(ABORT, 'disk full'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        await worker(case).run_once("binding")
    assert len(case[6].sent) == 1
    record = case[4].get("binding", row.delivery.delivery_id)
    assert record.state == "sending" and record.message_id == ""
    case[5].now = record.lease_until_ms
    assert await worker(case).run_once("binding") is None
    assert case[4].get("binding", row.delivery.delivery_id).state == "unknown"


async def test_intake_barrier_between_preflight_and_write(case):
    row = enqueue(case)
    case[6].on_preflight = lambda: SqliteHostingRecoveryStore(case[3]).block_intake(
        "binding"
    )
    assert (await worker(case).run_once("binding")).state == "queued"
    assert case[6].sent == []
    case[5].now += 10000
    assert await worker(case).run_once("binding") is None
    assert case[4].get("binding", row.delivery.delivery_id).attempts == 1


@pytest.mark.parametrize(
    "suffix", ["\n", "x" * 81, "\u202e", "\u2028", "\u2029", "\x00", 1]
)
def test_unsafe_ai_suffix_rejected(case, suffix):
    with pytest.raises(ValueError):
        enqueue(case, label_suffix=suffix)


@pytest.mark.parametrize("action", ["ignore", "escalate", "manual"])
def test_ignore_has_no_outbox_owner_notices_never_choose_peer(tmp_path, action):
    state, ledger, turn, directory = build_execution(tmp_path)
    if action == "manual":
        ledger.reserve(turn, invocation_for(turn))
        ledger.require_manual("binding", turn.turn_id, "invalid_result", ended=True)
    else:
        resolve(ledger, turn, action=action)
    row = SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=10000)
    if action == "ignore":
        assert row is None
    else:
        assert (
            row.delivery.purpose == "owner_notice"
            and row.delivery.binding.account_id == "self"
        )
        assert "需要你处理" in row.delivery.text
        assert turn.conversation_id not in row.delivery.text
        assert turn.turn_id not in row.delivery.text
        assert "消息摘录：" in row.delivery.text
        assert "请打开钉钉查看并回复" in row.delivery.text
        assert not row.delivery.text.startswith(AI_LABEL)


def test_migration_v3_preserves_resolved_execution(case):
    with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
        db.execute("DROP TABLE outbox")
        db.execute("DROP TABLE hosting_resolutions")
        db.execute("ALTER TABLE inbox DROP COLUMN received_ms")
        db.execute("ALTER TABLE executions DROP COLUMN review_json")
        db.execute("PRAGMA user_version = 3")
    outbox = SqliteHostingOutbox(case[3])
    assert case[1].get("binding", case[2].turn_id).state == "resolved"
    assert outbox.enqueue("binding", case[2].turn_id, now_ms=10000).state == "queued"


def test_scope_and_invalid_clock_are_rejected(case):
    row = enqueue(case)
    with pytest.raises(KeyError):
        case[4].get("other-binding", row.delivery.delivery_id)
    with pytest.raises(KeyError):
        case[4].claim("missing", now_ms=10000)
    with pytest.raises(ValueError):
        case[4].claim("binding", now_ms=True)


@pytest.mark.parametrize("timeout", [0, -1, True])
def test_invalid_worker_timeout(case, timeout):
    with pytest.raises(ValueError):
        HostingDeliveryWorker(case[4], case[6], case[5], timeout_ms=timeout)


@pytest.mark.parametrize("delays", [(0,), (-1,), (1,) * 11])
def test_invalid_retry_policy(delays):
    with pytest.raises(ValueError):
        DeliveryPolicy(retry_delays_ms=delays)


@pytest.mark.parametrize(
    "failure", [TimeoutError, RuntimeError, asyncio.CancelledError]
)
async def test_preflight_fault_never_dispatches(case, failure):
    row = enqueue(case)

    def fail():
        raise failure()

    case[6].on_preflight = fail
    if failure is TimeoutError:
        await worker(case).run_once("binding")
    else:
        with pytest.raises(failure):
            await worker(case).run_once("binding")
    result = case[4].get("binding", row.delivery.delivery_id)
    assert result.state == ("manual" if failure is RuntimeError else "queued")
    assert case[6].sent == []


async def test_real_async_deadline_before_send_is_retryable(case):
    enqueue(case)

    async def hung_preflight(delivery):
        await asyncio.Event().wait()

    case[6].preflight = hung_preflight
    job = HostingDeliveryWorker(case[4], case[6], case[5], timeout_ms=10)
    assert (await job.run_once("binding")).state == "queued"
    assert case[6].sent == []


def test_unknown_execution_cannot_be_enqueued(tmp_path):
    _, ledger, turn, directory = build_execution(tmp_path)
    ledger.reserve(turn, invocation_for(turn))
    assert (
        SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=10000)
        is None
    )


def test_expired_preflight_budget_releases_without_send(case):
    row = enqueue(case)
    outbox = SqliteHostingOutbox(case[3], DeliveryPolicy(retry_delays_ms=()))
    claimed = outbox.claim("binding", now_ms=case[5]())
    assert outbox.start_send(claimed, now_ms=claimed.lease_until_ms) is None
    assert outbox.claim("binding", now_ms=claimed.lease_until_ms) is None
    assert outbox.get("binding", row.delivery.delivery_id).state == "manual"
    assert ready_more(case[0]) is not None


def test_invalid_preflight_flag_rejected(case):
    enqueue(case)
    claim = case[4].claim("binding", now_ms=case[5]())
    with pytest.raises(ValueError):
        case[4].preflight_failed(claim, now_ms=case[5](), retryable=1)


async def test_stale_poll_cannot_overwrite_reclaimed_receipt(case):
    row = enqueue(case)
    outbox, clock, sender = case[4:]
    sender.receipt = DeliveryReceipt(status="accepted", task_id="task")
    pending = await worker(case).run_once("binding")
    first = outbox.claim("binding", now_ms=pending.due_ms)
    second = outbox.claim("binding", now_ms=first.lease_until_ms)
    assert second.state == "checking" and second.claim_id != first.claim_id
    success = DeliveryReceipt(
        status="sent", task_id="task", message_id="m", conversation_id="chat"
    )
    outbox.complete(first, success, now_ms=first.lease_until_ms)
    assert outbox.get("binding", row.delivery.delivery_id).state == "checking"
    outbox.complete(second, success, now_ms=first.lease_until_ms)
    assert outbox.get("binding", row.delivery.delivery_id).state == "sent"
    outbox.complete(
        second, DeliveryReceipt(status="unknown"), now_ms=first.lease_until_ms + 1
    )
    assert outbox.get("binding", row.delivery.delivery_id).state == "sent"
    assert len(sender.sent) == 1


async def test_crashed_poll_budget_is_not_infinite(case):
    row = enqueue(case)
    outbox = SqliteHostingOutbox(case[3], DeliveryPolicy(max_polls=1))
    case[6].receipt = DeliveryReceipt(status="accepted", task_id="task")
    pending = await worker(case).run_once("binding")
    first = outbox.claim("binding", now_ms=pending.due_ms)
    assert outbox.claim("binding", now_ms=first.lease_until_ms) is None
    assert outbox.get("binding", row.delivery.delivery_id).state == "unknown"
    assert ready_more(case[0]) is None


async def test_definitive_rejection_records_manual_no_retry(case):
    enqueue(case)
    case[6].receipt = DeliveryReceipt(status="rejected")
    assert (await worker(case).run_once("binding")).state == "manual"
    assert await worker(case).run_once("binding") is None
    assert ready_more(case[0]) is not None


async def test_persisting_send_reservation_failure_does_not_send(case):
    row = enqueue(case)
    with sqlite3.connect(case[3] / "dws_hosting.sqlite3") as db:
        db.execute("""CREATE TRIGGER deny_send BEFORE UPDATE ON outbox
                      WHEN NEW.state = 'sending' BEGIN SELECT RAISE(ABORT, 'disk full'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        await worker(case).run_once("binding")
    assert case[6].sent == []
    assert case[4].get("binding", row.delivery.delivery_id).state == "claimed"


async def test_failed_status_query_keeps_original_task_and_never_resends(case):
    enqueue(case)
    case[6].receipt = DeliveryReceipt(status="accepted", task_id="task")
    pending = await worker(case).run_once("binding")
    case[5].now = pending.due_ms

    async def failed_inspection(delivery, task_id):
        assert task_id == "task"
        raise RuntimeError("network unavailable")

    case[6].inspect = failed_inspection
    result = await worker(case).run_once("binding")
    assert result.state == "awaiting" and result.task_id == "task"
    assert len(case[6].sent) == 1
