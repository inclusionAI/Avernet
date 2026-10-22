"""Maintenance/migration/operational faults use real SQLite and scoped identities."""

import sqlite3
from types import SimpleNamespace

import pytest

from engine.community.kernel.dws_runtime import (
    HostingCapacityError,
    Resolution,
    RuntimePolicy,
)
from engine.community.plugins.dws_hosting.database import HostingDatabase
from engine.community.plugins.dws_hosting.runtime import SqliteHostingRuntimeStore
from engine.community.plugins.dws_hosting import capacity

from .test_delivery import resolve
from .test_execution import build_execution
from .test_runtime import runtime_case
from .test_state_contract import identity, message


def test_wrong_identity_and_bad_limits(tmp_path):
    _, _, _, directory = build_execution(tmp_path)
    ops = SqliteHostingRuntimeStore(directory)
    with pytest.raises(ValueError, match="identity"):
        ops.status(identity(bot_id="wrong"))
    for limit in (0, 101, True):
        with pytest.raises(ValueError):
            ops.pending(identity(), limit=limit)
        with pytest.raises(ValueError):
            ops.issues(identity(), limit=limit, offset=0)
    with pytest.raises(ValueError):
        ops.issues(identity(), limit=10, offset=-1)
    assert ops.issues(identity(), limit=10, offset=0) == ()


def test_restart_recovers_preflight_and_inspection_not_write(tmp_path):
    runtime, _, ledger, turn, directory, _, _, _, clock = runtime_case(tmp_path)
    resolve(ledger, turn)
    row = runtime.outbox.enqueue("binding", turn.turn_id, now_ms=clock())
    first = runtime.outbox.claim("binding", now_ms=clock())
    runtime.operations.prepare_restart(identity(), now_ms=clock())
    assert runtime.outbox.get("binding", row.delivery.delivery_id).state == "queued"
    assert runtime.outbox.start_send(first, now_ms=clock()) is None
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute(
            "UPDATE outbox SET state = 'checking', task_id = 't', claim_id = 'old'"
        )
    runtime.operations.prepare_restart(identity(), now_ms=clock())
    record = runtime.outbox.get("binding", row.delivery.delivery_id)
    assert (
        record.state == "awaiting" and record.task_id == "t" and record.claim_id == ""
    )


def test_revoked_unreserved_batch_does_not_block_after_resuming(tmp_path):
    runtime, state, _, turn, directory, _, _, _, clock = runtime_case(tmp_path)
    state.set_takeover("binding", "chat", True, now_ms=clock())
    assert runtime.operations.pending(identity(), limit=10) == ()
    state.set_takeover("binding", "chat", False, now_ms=clock())
    state.ingest("binding", message("new", created=11000), now_ms=11000)
    assert state.claim_ready("binding", now_ms=15000) is not None
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert (
            db.execute(
                "SELECT state FROM batches WHERE batch_id = ?", (turn.turn_id,)
            ).fetchone()[0]
            == "cancelled"
        )


def test_disk_reserve_failure_does_not_consume_new_message(tmp_path, monkeypatch):
    _, state, _, _, directory, _, _, _, _ = runtime_case(tmp_path)
    monkeypatch.setattr(
        capacity.shutil, "disk_usage", lambda _: SimpleNamespace(free=0)
    )
    with pytest.raises(HostingCapacityError, match="disk_reserve"):
        state.ingest("binding", message("new"), now_ms=10000)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1


def test_v5_migration_preserves_records_and_constraints(tmp_path):
    state, _, turn, directory = build_execution(tmp_path)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("DROP TABLE hosting_resolutions")
        db.execute("ALTER TABLE inbox DROP COLUMN received_ms")
        db.execute("PRAGMA user_version = 5")
    HostingDatabase(directory)
    assert state.get_turn("binding", turn.turn_id) == turn
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert db.execute("SELECT received_ms FROM inbox").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_v6_migration_rolls_back_column_and_version(tmp_path):
    _, _, _, directory = build_execution(tmp_path)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("ALTER TABLE inbox DROP COLUMN received_ms")
        db.execute("PRAGMA user_version = 5")
    # Intentionally corrupt fixture: the destination table already exists.
    with pytest.raises(sqlite3.OperationalError):
        HostingDatabase(directory)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert "received_ms" not in {
            row[1] for row in db.execute("PRAGMA table_info(inbox)")
        }


def test_operator_denied_or_bad_evidence_has_no_state_change(tmp_path):
    runtime, _, _, turn, _, _, _, _, clock = runtime_case(tmp_path)
    request = Resolution(kind="execution", target_id=turn.turn_id, evidence="checked")
    with pytest.raises(PermissionError):
        runtime.operations.resolve_manual(
            identity(), request, actor_id="stranger", now_ms=clock()
        )
    with pytest.raises(ValueError, match="evidence"):
        runtime.operations.resolve_manual(
            identity(),
            request.model_copy(update={"evidence": " "}),
            actor_id=identity().owner_id,
            now_ms=clock(),
        )
    with pytest.raises(ValueError, match="unresolved"):
        runtime.operations.resolve_manual(
            identity(), request, actor_id=identity().owner_id, now_ms=clock()
        )


async def test_issue_projection_is_scoped_and_does_not_leak_text(tmp_path):
    runtime, _, ledger, turn, _, _, engine, _, _ = runtime_case(tmp_path)
    from engine.community.core.dws_hosting.execution import invocation_for

    ledger.reserve(turn, invocation_for(turn))
    ledger.mark_unknown("binding", turn.turn_id, "unknown")
    with pytest.raises(PermissionError):
        await runtime.issues("stranger")
    issues = await runtime.issues(identity().owner_id)
    assert len(issues) == 1
    assert issues[0].target_id == turn.turn_id
    assert set(issues[0].model_dump()) == {
        "kind",
        "target_id",
        "turn_id",
        "conversation_id",
        "state",
    }
    assert await runtime.issues(identity().owner_id, offset=1) == ()


def test_cleanup_unbatched_ignored_payload_preserves_tombstone(tmp_path):
    runtime, state, _, _, directory, _, _, _, clock = runtime_case(tmp_path)
    state.set_enabled("binding", False, now_ms=clock())
    state.ingest("binding", message("ignored"), now_ms=clock())
    runtime.operations.maintain(identity(), now_ms=clock() + 31 * 86_400_000)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert (
            db.execute(
                "SELECT message_json FROM inbox WHERE message_id = 'ignored'"
            ).fetchone()[0]
            == ""
        )
    assert state.ingest("binding", message("ignored"), now_ms=clock()) == "duplicate"


async def test_resolution_transaction_failure_keeps_unknown_and_no_partial_takeover(
    tmp_path,
):
    runtime, state, ledger, turn, directory, _, engine, _, _ = runtime_case(tmp_path)
    from engine.community.core.dws_hosting.execution import invocation_for

    ledger.reserve(turn, invocation_for(turn))
    ledger.mark_unknown("binding", turn.turn_id, "unknown")
    engine.ended = True
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute(
            "CREATE TRIGGER reject_ack BEFORE INSERT ON hosting_resolutions BEGIN SELECT RAISE(ABORT, 'full'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await runtime.resolve_manual(
            identity().owner_id,
            Resolution(kind="execution", target_id=turn.turn_id, evidence="checked"),
        )
    assert ledger.get("binding", turn.turn_id).state == "unknown"
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT taken_over FROM conversations").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,value",
    [("tick_seconds", 0), ("retention_ms", 1), ("max_pending", 0), ("max_inbox", True)],
)
def test_policy_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        RuntimePolicy(**{field: value})
