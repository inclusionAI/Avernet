"""DWS send contract: real offline CLI process and strict receipt projections."""

import json
import os

import pytest
from pydantic import ValidationError

from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.kernel.dws_connection import DwsError
from engine.community.kernel.dws_delivery import (
    Delivery,
    DeliveryReceipt,
    PreflightError,
)
from engine.community.plugins.dws_hosting.delivery_wire import receipt
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from engine.community.plugins.dws_hosting.sender import DwsCliSender

from .test_cli_contract import connection
from .test_delivery import Clock, resolve
from .test_execution import build_execution
from .test_state_contract import identity


def delivery(**changes):
    return Delivery(
        **(
            dict(
                delivery_id="stable-id",
                turn_id="turn",
                binding=identity(),
                conversation_id="chat",
                purpose="reply",
                text="hello",
            )
            | changes
        )
    )


def sent_projection(**changes):
    return (
        dict(
            contractVersion="im.message-send-receipt.v1",
            openTaskId="task",
            readyForMessageActions=True,
            messageRef={"openMessageId": "message", "openConversationId": "chat"},
        )
        | changes
    )


def send_result(**changes):
    return (
        dict(
            ok=True,
            identity="user",
            tool="send_personal_message",
            sendReceipt=sent_projection(),
            result={},
        )
        | changes
    )


async def test_real_subprocess_send_status_and_mandatory_markers(tmp_path):
    client = connection(tmp_path)
    sender = DwsCliSender(client, identity())
    body = '--ai-tag=false $(touch forbidden) " @./private.json'
    item = delivery(text=body)
    await sender.preflight(item)
    result = await sender.send(item)
    assert (
        result.status == "accepted"
        and result.task_id == "task"
        and result.message_id == ""
    )
    result = await sender.inspect(item, result.task_id)
    assert result.status == "sent" and result.message_id == "delivered"
    argv = [
        json.loads(line)
        for line in (client.process.config_dir / "argv.jsonl").read_text().splitlines()
    ]
    send = argv[1]
    assert send[:2] == ["chat", "+messages-send"]
    assert "--text=" + body in send
    assert "--ai-tag=true" in send and "--idempotency-key=stable-id" in send
    assert "--chat-id=chat" in send and "--as=user" in send
    assert all(command[-2:] == ["--profile", "org:self"] for command in argv[1:])
    assert argv[2][:3] == ["chat", "+messages-query-send-status", "--open-task-id=task"]
    assert not (client.process.config_dir / "forbidden").exists()


async def test_owner_notice_requires_dedicated_notifier_never_dws_self_or_peer(
    tmp_path,
):
    client = connection(tmp_path)
    sender = DwsCliSender(client, identity())
    item = delivery(purpose="owner_notice")
    for operation in (sender.preflight, sender.send):
        with pytest.raises(PreflightError, match="owner_notifier_required"):
            await operation(item)
    with pytest.raises(PreflightError, match="owner_notifier_required"):
        await sender.inspect(item, "task")
    assert not (client.process.config_dir / "argv.jsonl").exists()


@pytest.mark.parametrize(
    "mode,retryable", [("logged-out", False), ("fail", True), ("invalid", False)]
)
async def test_preflight_failure_is_before_send(tmp_path, mode, retryable):
    client = connection(tmp_path, mode)
    with pytest.raises(PreflightError) as error:
        await DwsCliSender(client, identity()).preflight(delivery())
    assert error.value.retryable == retryable
    assert "private diagnostic" not in str(error.value)
    args = json.loads((client.process.config_dir / "argv.jsonl").read_text())
    assert args[:2] == ["auth", "status"]


async def test_sender_rejects_cross_bot_and_account_without_subprocess(tmp_path):
    client = connection(tmp_path)
    with pytest.raises(ValueError):
        DwsCliSender(client, identity(account_id="other"))
    sender = DwsCliSender(client, identity())
    for operation in (sender.preflight, sender.send):
        with pytest.raises(PreflightError):
            await operation(delivery(binding=identity(bot_id="other")))
    with pytest.raises(PreflightError):
        await sender.inspect(delivery(binding=identity(bot_id="other")), "task")
    assert not (client.process.config_dir / "argv.jsonl").exists()


@pytest.mark.parametrize("mode", ["send-fail", "send-malformed", "send-hang"])
async def test_failed_cli_write_stays_unknown_no_repeat(tmp_path, mode):
    state, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn)
    outbox, clock = SqliteHostingOutbox(directory), Clock()
    row = outbox.enqueue("binding", turn.turn_id, now_ms=clock())
    client = connection(tmp_path, mode)
    client.process.timeout = 1
    job = HostingDeliveryWorker(outbox, DwsCliSender(client, identity()), clock)
    assert (await job.run_once("binding")).state == "unknown"
    clock.now += 100000
    assert await job.run_once("binding") is None
    assert outbox.get("binding", row.delivery.delivery_id).state == "unknown"
    argv = [
        json.loads(line)
        for line in (client.process.config_dir / "argv.jsonl").read_text().splitlines()
    ]
    assert sum(a[:2] == ["chat", "+messages-send"] for a in argv) == 1
    pid = int((client.process.config_dir / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_real_subprocess_full_worker_roundtrip(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn)
    outbox, clock = SqliteHostingOutbox(directory), Clock()
    outbox.enqueue("binding", turn.turn_id, now_ms=clock())
    job = HostingDeliveryWorker(
        outbox, DwsCliSender(connection(tmp_path), identity()), clock
    )
    first = await job.run_once("binding")
    assert first.state == "awaiting"
    clock.now = first.due_ms
    assert (await job.run_once("binding")).state == "sent"
    assert state.get_turn("binding", turn.turn_id) == turn


async def test_real_subprocess_immediate_confirmation(tmp_path):
    sender = DwsCliSender(connection(tmp_path, "send-direct"), identity())
    result = await sender.send(delivery())
    assert result.status == "sent" and result.conversation_id == "chat"


async def test_real_subprocess_pending_status_never_confirms_delivery(tmp_path):
    sender = DwsCliSender(connection(tmp_path, "poll-pending"), identity())
    result = await sender.inspect(delivery(), "task")
    assert result.status == "unknown" and result.task_id == "task"


@pytest.mark.parametrize(
    "changes",
    [
        {"ok": False},
        {"identity": "bot"},
        {"tool": "wrong"},
        {"sendReceipt": None},
        {"sendReceipt": sent_projection(contractVersion="new")},
        {"sendReceipt": sent_projection(openTaskId=3)},
        {"sendReceipt": sent_projection(messageRef=[])},
        {"sendReceipt": sent_projection(messageRef={})},
    ],
)
def test_invalid_send_receipt_rejected(changes):
    with pytest.raises((DwsError, ValidationError)):
        receipt(json.dumps(send_result(**changes)))


@pytest.mark.parametrize(
    "raw", ["bad", "[]", json.dumps({"ok": False, "outcome": "failed", "data": {}})]
)
def test_invalid_output_is_not_a_delivery_ack(raw):
    with pytest.raises(DwsError):
        receipt(raw)


@pytest.mark.parametrize(
    "result",
    [
        {"status": "PENDING"},
        {"status": "FAILED"},
        {"status": "new-enum"},
        {"success": False},
        {"ok": "true"},
        {"error": "private"},
    ],
)
def test_negative_or_unknown_remote_state_not_reported_sent(result):
    assert receipt(json.dumps(send_result(result=result))).status == "unknown"


def test_receipt_not_task_id_means_sent():
    value = receipt(json.dumps(send_result()))
    assert value.status == "sent" and value.message_id == "message"
    value = receipt(
        json.dumps(
            send_result(
                sendReceipt=sent_projection(readyForMessageActions=False, messageRef={})
            )
        )
    )
    assert value.status == "accepted" and value.message_id == ""
    value = receipt(
        json.dumps(
            send_result(
                sendReceipt=sent_projection(
                    readyForMessageActions=False, openTaskId="", messageRef={}
                )
            )
        )
    )
    assert value.status == "unknown"


def test_status_is_bound_to_requested_task():
    raw = dict(
        contractVersion="im.message-send-status.v1",
        openTaskId="task",
        readyForMessageActions=False,
    )
    assert receipt(json.dumps(raw), task_id="task").status == "accepted"
    with pytest.raises(DwsError):
        receipt(json.dumps(raw), task_id="other")
    raw.update(
        readyForMessageActions=True,
        messageRef={"openMessageId": "m", "openConversationId": "chat"},
    )
    assert receipt(json.dumps(raw), task_id="task").status == "sent"


@pytest.mark.parametrize(
    "values",
    [
        dict(status="sent"),
        dict(status="accepted"),
        dict(status="sent", message_id="m", conversation_id="\x00"),
    ],
)
def test_delivery_receipt_requires_real_identifiers(values):
    with pytest.raises(ValidationError):
        DeliveryReceipt(**values)


def test_reply_body_does_not_require_a_textual_ai_prefix():
    assert delivery(text="hello").text == "hello"


def test_deep_unrecognized_response_not_reported_sent():
    nested = {"status": "SUCCESS"}
    for _ in range(10):
        nested = {"result": nested}
    assert receipt(json.dumps(send_result(result=nested))).status == "unknown"
