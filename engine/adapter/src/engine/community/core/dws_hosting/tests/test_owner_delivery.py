"""Purpose routing and provider acceptance are independent of peer delivery."""

import sqlite3

import pytest

from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.senders import HostingDeliveryRouter
from engine.community.kernel.dws_delivery import DeliveryReceipt
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from engine.community.plugins.dws_hosting.outbox_schema import OUTBOX_SCHEMA

from .test_delivery import Clock, Sender, resolve
from .test_execution import build_execution, ready_more


@pytest.mark.parametrize("action", ["reply", "escalate"])
async def test_router_preserves_reply_semantics_and_owner_acceptance(tmp_path, action):
    state, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn, action=action)
    outbox, clock = SqliteHostingOutbox(directory), Clock()
    outbox.enqueue("binding", turn.turn_id, now_ms=clock())
    replies, owners = Sender(), Sender()
    owners.receipt = DeliveryReceipt(
        status="accepted", task_id="owner-task", queryable=False
    )
    router = HostingDeliveryRouter(replies, owners)
    worker = HostingDeliveryWorker(outbox, router, clock)
    row = await worker.run_once("binding")
    if action == "reply":
        assert row.state == "sent" and len(replies.sent) == 1
        assert owners.sent == owners.preflights == []
    else:
        assert row.state == "accepted" and row.reason == "accepted_unverified"
        assert (
            row.task_id == "owner-task"
            and row.message_id == row.remote_conversation_id == ""
        )
        assert len(owners.sent) == 1 and replies.sent == replies.preflights == []
        assert owners.inspected == []
    clock.now += 100000
    assert await worker.run_once("binding") is None
    assert ready_more(state) is not None
    # Persist the distinction across restart; never promote accepted to sent.
    assert (
        SqliteHostingOutbox(directory).get("binding", row.delivery.delivery_id) == row
    )


async def test_no_query_support_does_not_settle_peer_reply(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn)
    outbox, sender = SqliteHostingOutbox(directory), Sender()
    outbox.enqueue("binding", turn.turn_id, now_ms=10000)
    sender.receipt = DeliveryReceipt(status="accepted", task_id="task", queryable=False)
    row = await HostingDeliveryWorker(outbox, sender, Clock()).run_once("binding")
    assert row.state == "unknown" and row.reason == "receipt_not_queryable"
    assert ready_more(state) is None


@pytest.mark.parametrize("action", ["reply", "escalate"])
async def test_router_poll_uses_same_purpose(tmp_path, action):
    _, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn, action=action)
    item = (
        SqliteHostingOutbox(directory)
        .enqueue("binding", turn.turn_id, now_ms=10000)
        .delivery
    )
    replies, owners = Sender(), Sender()
    router = HostingDeliveryRouter(replies, owners)
    await router.inspect(item, "t")
    selected, untouched = (replies, owners) if action == "reply" else (owners, replies)
    assert selected.inspected == [(item, "t")] and untouched.inspected == []


def test_unqueryable_flag_requires_accepted_receipt():
    with pytest.raises(ValueError):
        DeliveryReceipt(status="unknown", queryable=False)


def downgrade_to_v4(directory):
    # The old CHECK constraint, not merely an edited user_version.
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("ALTER TABLE outbox RENAME TO outbox_new")
        db.execute("DROP INDEX outbox_due")
        db.execute(OUTBOX_SCHEMA.replace(", 'accepted'", "").replace(",'accepted'", ""))
        db.execute("INSERT INTO outbox SELECT * FROM outbox_new")
        db.execute("DROP TABLE outbox_new")
        db.execute("CREATE INDEX outbox_due ON outbox(binding_id, state, due_ms)")
        db.execute("DROP TABLE hosting_resolutions")
        db.execute("ALTER TABLE inbox DROP COLUMN received_ms")
        db.execute("ALTER TABLE executions DROP COLUMN review_json")
        db.execute("PRAGMA user_version = 4")


@pytest.mark.parametrize(
    "phase", ["queued", "sending", "awaiting", "unknown", "sent", "manual", "cancelled"]
)
def test_v4_migration_preserves_pending_and_final_records(tmp_path, phase):
    _, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn)
    outbox = SqliteHostingOutbox(directory)
    item = outbox.enqueue("binding", turn.turn_id, now_ms=10000).delivery
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute(
            "UPDATE outbox SET state = ?, task_id = ?, due_ms = ?",
            (phase, "saved-task", 13000),
        )
    before = outbox.get("binding", item.delivery_id)
    downgrade_to_v4(directory)
    after = SqliteHostingOutbox(directory).get("binding", item.delivery_id)
    assert before == after
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_failure_does_not_partially_change_v4(tmp_path):
    _, ledger, turn, directory = build_execution(tmp_path)
    resolve(ledger, turn)
    SqliteHostingOutbox(directory).enqueue("binding", turn.turn_id, now_ms=10000)
    downgrade_to_v4(directory)
    path = directory / "dws_hosting.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("DROP INDEX outbox_due")
    with pytest.raises(sqlite3.OperationalError):
        SqliteHostingOutbox(directory)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name = 'outbox_v4'"
            ).fetchall()
            == []
        )
