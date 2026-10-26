"""Real SQLite recovery tests: crashes, late notifications, fencing and migration."""

import sqlite3

import pytest

from engine.community.kernel.dws_connection import ListenerNotice
from engine.community.plugins.dws_hosting.database import SCHEMA
from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore

from .test_state_contract import identity, message


@pytest.fixture
def stores(tmp_path):
    directory = tmp_path / "state"
    state = SqliteHostingStateStore(directory)
    state.bind(identity())
    state.confirm_rule("binding", "rule")
    state.set_enabled("binding", True, now_ms=1000)
    return state, SqliteHostingRecoveryStore(directory), directory


def window(recovery, now=100_000):
    return recovery.recovery_window(
        "binding", now_ms=now, overlap_ms=30_000, window_ms=60_000
    )


def hint(mid="m1", created=1100, chat="chat"):
    return ListenerNotice(
        kind="conversation", conversation_id=chat, message_id=mid, created_at_ms=created
    )


def test_barrier_requires_full_catchup(stores):
    state, recovery, _ = stores
    state.ingest("binding", message(), now_ms=2000)
    recovery.block_intake("binding")
    assert state.claim_ready("binding", now_ms=7000) is None
    first = window(recovery)
    assert (first.start_ms, first.end_ms) == (1000, 61000)
    assert not recovery.complete_recovery(first)
    second = window(recovery)
    assert second.start_ms == 31000
    assert not recovery.complete_recovery(second)
    last = window(recovery)
    assert recovery.complete_recovery(last)
    assert state.claim_ready("binding", now_ms=100_000) is not None


def test_hints_persist_until_history_proves_message_present(stores):
    state, recovery, directory = stores
    state.ingest("binding", message(), now_ms=2000)
    recovery.record_hint("binding", hint())
    recovery = SqliteHostingRecoveryStore(directory)
    first = window(recovery, 5000)
    assert recovery.complete_recovery(first)
    assert state.claim_ready("binding", now_ms=7000) is None
    retry = window(recovery, 6000)
    assert retry.start_ms == 1000
    recovery.resolve_hints(retry, "wrong-chat", ("m1",))
    assert recovery.complete_recovery(retry)
    assert state.claim_ready("binding", now_ms=7000) is None
    retry = window(recovery, 7000)
    recovery.resolve_hints(retry, "chat", ("m1",))
    assert recovery.complete_recovery(retry)
    assert state.claim_ready("binding", now_ms=7000) is not None


def test_new_hint_during_recovery_is_not_erased(stores):
    state, recovery, _ = stores
    state.ingest("binding", message(), now_ms=2000)
    first = window(recovery, 5000)
    recovery.record_hint("binding", hint())
    recovery.resolve_hints(first, "chat", ("m1",))
    assert recovery.complete_recovery(first)
    assert state.claim_ready("binding", now_ms=7000) is None
    next_window = window(recovery, 6000)
    recovery.resolve_hints(next_window, "chat", ("m1",))
    assert recovery.complete_recovery(next_window)


def test_current_second_hint_keeps_claim_barrier_closed(stores):
    state, recovery, _ = stores
    state.ingest("binding", message(), now_ms=2000)
    first = window(recovery, 5000)
    recovery.record_hint("binding", hint("new", created=5500))
    assert recovery.complete_recovery(first)
    assert state.claim_ready("binding", now_ms=7000) is None
    later = window(recovery, 6000)
    recovery.resolve_hints(later, "chat", ("new",))
    assert recovery.complete_recovery(later)


def test_delayed_event_older_than_overlap_is_revisited(stores):
    _, recovery, _ = stores
    while not recovery.complete_recovery(window(recovery)):
        pass
    recovery.record_hint("binding", hint(created=1500))
    late = recovery.pending_hints("binding", now_ms=100_000)[0]
    assert late.start_ms == 1000
    recovery.complete_hints(late, ("m1",))
    assert not recovery.pending_hints("binding", now_ms=100_000)
    assert window(recovery) is None


def test_epoch_disconnect_and_concurrent_attempt_fence_checkpoint(stores):
    state, recovery, _ = stores
    first = window(recovery, 5000)
    recovery.block_intake("binding")
    assert not recovery.complete_recovery(first)
    first, second = window(recovery, 5000), window(recovery, 5000)
    assert not recovery.complete_recovery(first)
    state.set_enabled("binding", False, now_ms=6000)
    assert not recovery.complete_recovery(second)
    with pytest.raises(ValueError, match="disabled"):
        window(recovery)
    state.set_enabled("binding", True, now_ms=7000)
    assert state.claim_ready("binding", now_ms=10_000) is None
    assert window(recovery, 10_000).start_ms == 7000


def test_known_conversations_and_hint_dedup(stores):
    state, recovery, _ = stores
    recovery.remember_conversation("binding", "old")
    for _ in range(3):
        recovery.record_hint("binding", hint())
    assert recovery.known_conversations("binding") == ("chat", "old")
    assert window(recovery, 5000).hint_limit == 1
    state.set_enabled("binding", False, now_ms=6000)
    recovery.record_hint("binding", hint(chat="disabled"))
    state.set_enabled("binding", True, now_ms=7000)
    recovery.record_hint("binding", hint(chat="old-hint"))
    assert recovery.known_conversations("binding") == ("chat", "old")
    assert recovery.complete_recovery(window(recovery, 10_000))


def test_invalid_recovery_inputs(stores):
    _, recovery, _ = stores
    with pytest.raises(ValueError):
        recovery.record_hint("binding", ListenerNotice(kind="ready"))
    with pytest.raises(ValueError):
        recovery.remember_conversation("binding", "")
    with pytest.raises(ValueError):
        recovery.recovery_window(
            "binding", now_ms=5000, overlap_ms=1000, window_ms=1000
        )
    assert window(recovery, 1000) is None
    assert recovery.complete_recovery(window(recovery, 5000))
    assert window(recovery, 5000) is None


def test_migrate_v1_without_losing_identity(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    path = directory / "dws_hosting.sqlite3"
    path.touch(mode=0o600)
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("PRAGMA application_id = 1146573640")
        db.execute("PRAGMA user_version = 1")
    state = SqliteHostingStateStore(directory)
    state.bind(identity())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("SELECT count(*) FROM bindings").fetchone()[0] == 1
