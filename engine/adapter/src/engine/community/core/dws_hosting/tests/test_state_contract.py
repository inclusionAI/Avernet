"""HostingStateStore conformance tests using real SQLite, no network/engine."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from engine.community.kernel.dws_hosting import BatchPolicy, Binding, DirectMessage
from engine.community.plugin_api.dws_hosting import HostingStateStore
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore


def identity(**changes) -> Binding:
    return Binding(
        **(
            dict(
                binding_id="binding",
                bot_id="bot",
                owner_id="owner",
                account_id="self",
                environment="test",
                organization_id="org",
                agent_id="main",
            )
            | changes
        ),
    )


def message(mid="m1", *, chat="chat", created=1_001, text="hello", sender="peer"):
    return DirectMessage(
        conversation_id=chat,
        message_id=mid,
        sender_id=sender,
        created_at_ms=created,
        text=text,
    )


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "hosting"


@pytest.fixture
def store(state_dir) -> HostingStateStore:
    store = SqliteHostingStateStore(state_dir)
    store.bind(identity())
    store.confirm_rule("binding", "Reply in the user's language.")
    store.set_enabled("binding", True, now_ms=1_000)
    return store


def test_disabled_by_default_and_requires_confirmed_rule(state_dir):
    store = SqliteHostingStateStore(state_dir)
    store.bind(identity())
    assert store.ingest("binding", message(), now_ms=2_000) == "ignored"
    assert store.claim_ready("binding", now_ms=10_000) is None
    with pytest.raises(ValueError, match="confirm a rule"):
        store.set_enabled("binding", True, now_ms=2_000)
    assert store.confirm_rule("binding", "New confirmed rule") == 1


def test_bind_retry_never_resets_state_or_changes_identity(store):
    store.bind(identity())
    assert store.confirm_rule("binding", "v2") == 2
    with pytest.raises(ValueError, match="immutable"):
        store.bind(identity(owner_id="other"))
    assert store.ingest("binding", message(), now_ms=2_000) == "queued"


def test_same_account_cannot_create_duplicate_binding_within_bot(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.bind(identity(binding_id="other"))


@pytest.mark.parametrize("scope", ["bot_id", "organization_id", "environment"])
def test_bindings_are_scoped_without_global_one_account_one_bot(store, scope):
    other = identity(binding_id="other", **{scope: "other"})
    store.bind(other)
    store.confirm_rule("other", "other rule")
    store.set_enabled("other", True, now_ms=1_000)
    assert store.ingest("binding", message(), now_ms=2_000) == "queued"
    assert store.ingest("other", message(), now_ms=2_000) == "queued"
    first = store.claim_ready("binding", now_ms=6_000)
    second = store.claim_ready("other", now_ms=6_000)
    assert first.session_id != second.session_id
    with pytest.raises(KeyError):
        store.get_turn("other", first.turn_id)


def test_quiet_window_and_snapshot(store):
    assert store.ingest("binding", message(), now_ms=2_000) == "queued"
    assert store.claim_ready("binding", now_ms=5_999) is None
    turn = store.claim_ready("binding", now_ms=6_000)
    assert turn.messages == (message(),)
    assert turn.rule_version == 1
    assert turn.binding == identity()
    assert store.is_current("binding", turn.turn_id)
    assert store.get_turn("binding", turn.turn_id) == turn
    assert store.claim_ready("binding", now_ms=7_000) is None


def test_duplicate_does_not_change_body_or_extend_window(store):
    store.ingest("binding", message(), now_ms=2_000)
    assert store.ingest("binding", message(text="changed"), now_ms=5_999) == "duplicate"
    turn = store.claim_ready("binding", now_ms=6_000)
    assert turn.messages == (message(),)


def test_new_messages_extend_quiet_but_never_fifteen_second_cap(store):
    for i, now in enumerate(range(2_000, 17_000, 3_000)):
        store.ingest("binding", message(str(i), created=now), now_ms=now)
    assert store.claim_ready("binding", now_ms=16_999) is None
    turn = store.claim_ready("binding", now_ms=17_000)
    assert len(turn.messages) == 5


def test_arrival_after_deadline_starts_new_batch_before_scheduler_runs(store):
    store.ingest("binding", message(), now_ms=2_000)
    store.ingest("binding", message("m2", created=6_000), now_ms=6_000)
    first = store.claim_ready("binding", now_ms=6_000)
    assert [m.message_id for m in first.messages] == ["m1"]
    assert store.claim_ready("binding", now_ms=10_000) is None
    store.finish_turn("binding", first.turn_id, "completed")
    second = store.claim_ready("binding", now_ms=10_000)
    assert [m.message_id for m in second.messages] == ["m2"]
    assert second.session_id == first.session_id


def test_batches_remain_bounded_by_message_count(state_dir):
    store = SqliteHostingStateStore(state_dir, BatchPolicy(max_messages=2))
    store.bind(identity())
    store.confirm_rule("binding", "rule")
    store.set_enabled("binding", True, now_ms=1_000)
    for i in range(3):
        store.ingest("binding", message(str(i)), now_ms=2_000)
    turn = store.claim_ready("binding", now_ms=2_000)
    assert len(turn.messages) == 2
    store.finish_turn("binding", turn.turn_id, "completed")
    assert store.claim_ready("binding", now_ms=2_000) is None
    assert len(store.claim_ready("binding", now_ms=6_000).messages) == 1


def test_character_limit_splits_and_freezes_exact_limit(state_dir):
    store = SqliteHostingStateStore(state_dir, BatchPolicy(max_characters=6))
    store.bind(identity())
    store.confirm_rule("binding", "rule")
    store.set_enabled("binding", True, now_ms=1_000)
    store.ingest("binding", message(text="aaaa"), now_ms=2_000)
    store.ingest("binding", message("m2", text="bbbbbb"), now_ms=2_001)
    first = store.claim_ready("binding", now_ms=2_001)
    assert first.messages[0].text == "aaaa"
    store.finish_turn("binding", first.turn_id, "completed")
    assert store.claim_ready("binding", now_ms=2_001).messages[0].text == "bbbbbb"
    assert (
        store.ingest("binding", message("large", text="1234567"), now_ms=3_000)
        == "ignored"
    )


def test_same_conversation_serialized_others_can_run(store):
    store.ingest("binding", message(), now_ms=2_000)
    first = store.claim_ready("binding", now_ms=6_000)
    store.ingest("binding", message("m2", created=6_001), now_ms=6_001)
    store.ingest("binding", message(chat="other", created=6_001), now_ms=6_001)
    other = store.claim_ready("binding", now_ms=10_001)
    assert other.conversation_id == "other"
    assert other.session_id != first.session_id
    assert store.claim_ready("binding", now_ms=10_001) is None


def test_latest_rule_selected_when_claimed_not_when_received(store):
    store.ingest("binding", message(), now_ms=2_000)
    assert store.confirm_rule("binding", "rule v2") == 2
    turn = store.claim_ready("binding", now_ms=6_000)
    assert (turn.rule_version, turn.rule_text) == (2, "rule v2")
    store.confirm_rule("binding", "rule v3")
    assert store.is_current("binding", turn.turn_id)
    assert store.get_turn("binding", turn.turn_id) == turn


def test_restart_keeps_deadline_rule_and_session_and_dedup(store, state_dir):
    store.ingest("binding", message(), now_ms=2_000)
    restarted = SqliteHostingStateStore(state_dir)
    assert restarted.ingest("binding", message(), now_ms=5_999) == "duplicate"
    turn = restarted.claim_ready("binding", now_ms=6_000)
    again = SqliteHostingStateStore(state_dir)
    assert again.get_turn("binding", turn.turn_id) == turn
    assert again.claim_ready("binding", now_ms=100_000) is None
    again.finish_turn("binding", turn.turn_id, "completed")
    again.ingest("binding", message("m2", created=100_001), now_ms=100_001)
    assert again.claim_ready("binding", now_ms=104_001).session_id == turn.session_id


def test_unknown_turn_blocks_after_restart_until_explicit_resolution(store, state_dir):
    store.ingest("binding", message(), now_ms=2_000)
    turn = store.claim_ready("binding", now_ms=6_000)
    store.finish_turn("binding", turn.turn_id, "unknown")
    store.finish_turn("binding", turn.turn_id, "unknown")
    assert not store.is_current("binding", turn.turn_id)
    store.ingest("binding", message("m2", created=7_000), now_ms=7_000)
    restarted = SqliteHostingStateStore(state_dir)
    assert restarted.claim_ready("binding", now_ms=11_000) is None
    restarted.finish_turn("binding", turn.turn_id, "completed")
    assert restarted.claim_ready("binding", now_ms=11_000) is not None


def test_disable_cancels_pending_and_fences_running_even_after_reenable(store):
    store.ingest("binding", message(), now_ms=2_000)
    turn = store.claim_ready("binding", now_ms=6_000)
    store.ingest("binding", message("m2", created=6_001), now_ms=6_001)
    store.set_enabled("binding", False, now_ms=7_000)
    assert not store.is_current("binding", turn.turn_id)
    assert (
        store.ingest("binding", message("disabled", created=7_001), now_ms=7_001)
        == "ignored"
    )
    store.set_enabled("binding", True, now_ms=8_000)
    assert not store.is_current("binding", turn.turn_id)
    assert (
        store.ingest("binding", message("disabled", created=7_001), now_ms=9_000)
        == "duplicate"
    )
    assert (
        store.ingest("binding", message("late", created=7_500), now_ms=9_000)
        == "ignored"
    )
    store.finish_turn("binding", turn.turn_id, "completed")
    assert store.claim_ready("binding", now_ms=20_000) is None
    store.ingest("binding", message("fresh", created=20_001), now_ms=20_001)
    assert store.claim_ready("binding", now_ms=24_001).messages[0].message_id == "fresh"


def test_takeover_scoped_to_conversation_and_old_work_never_revives(store):
    store.ingest("binding", message(), now_ms=2_000)
    store.ingest("binding", message(chat="other"), now_ms=2_000)
    store.set_takeover("binding", "chat", True, now_ms=3_000)
    assert (
        store.ingest("binding", message("during", created=4_000), now_ms=4_000)
        == "ignored"
    )
    assert store.claim_ready("binding", now_ms=6_000).conversation_id == "other"
    store.set_takeover("binding", "chat", False, now_ms=7_000)
    assert (
        store.ingest("binding", message("late", created=5_000), now_ms=8_000)
        == "ignored"
    )
    assert store.claim_ready("binding", now_ms=10_000) is None
    store.ingest("binding", message("fresh", created=10_001), now_ms=10_001)
    assert store.claim_ready("binding", now_ms=14_001).conversation_id == "chat"


def test_takeover_fences_running_turn_and_is_idempotent(store):
    store.ingest("binding", message(), now_ms=2_000)
    turn = store.claim_ready("binding", now_ms=6_000)
    store.set_enabled("binding", True, now_ms=7_000)
    store.set_takeover("binding", "chat", False, now_ms=7_000)
    assert store.is_current("binding", turn.turn_id)
    store.set_takeover("binding", "chat", True, now_ms=8_000)
    store.set_takeover("binding", "chat", True, now_ms=9_000)
    store.set_takeover("binding", "chat", False, now_ms=10_000)
    assert not store.is_current("binding", turn.turn_id)


@pytest.mark.parametrize(
    "changes",
    [dict(created=999), dict(created=1_000), dict(created=3_000), dict(sender="self")],
)
def test_old_future_and_own_messages_are_retained_but_not_executed(store, changes):
    msg = message(**changes)
    assert store.ingest("binding", msg, now_ms=2_000) == "ignored"
    assert store.ingest("binding", msg, now_ms=10_000) == "duplicate"
    assert store.claim_ready("binding", now_ms=15_000) is None


def test_history_pages_deduplicate_and_sort_without_modifying_input(store):
    later = message("later", created=1_900)
    earlier = message("earlier", created=1_100)
    store.ingest("binding", later, now_ms=2_000)
    store.ingest("binding", earlier, now_ms=2_001)
    store.ingest("binding", later, now_ms=2_002)
    assert store.claim_ready("binding", now_ms=6_001).messages == (earlier, later)


def test_concurrent_ingest_and_claim_use_database_atomicity(store, state_dir):
    stores = [SqliteHostingStateStore(state_dir) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(lambda s: s.ingest("binding", message(), now_ms=2_000), stores)
        )
        claims = list(
            pool.map(lambda s: s.claim_ready("binding", now_ms=6_000), stores)
        )
    assert outcomes.count("queued") == 1
    assert outcomes.count("duplicate") == 7
    assert sum(turn is not None for turn in claims) == 1


def test_ingest_failure_rolls_back_message_and_batch(store, state_dir):
    path = state_dir / "dws_hosting.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER reject_inbox BEFORE INSERT ON inbox BEGIN SELECT RAISE(ABORT, 'disk test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="disk test"):
        store.ingest("binding", message(), now_ms=2_000)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM batches").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_inbox")
    assert store.ingest("binding", message(), now_ms=2_000) == "queued"


def test_claim_failure_does_not_lose_batch(store, state_dir):
    store.ingest("binding", message(), now_ms=2_000)
    path = state_dir / "dws_hosting.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER reject_claim BEFORE UPDATE ON batches
                      WHEN NEW.state = 'running' BEGIN SELECT RAISE(ABORT, 'claim failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="claim failed"):
        store.claim_ready("binding", now_ms=6_000)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER reject_claim")
    assert store.claim_ready("binding", now_ms=6_000).messages == (message(),)


def test_identifiers_and_text_cannot_become_sql(store):
    unusual = message(
        "' OR 1=1; --",
        chat="' DROP TABLE bindings; --",
        text="'); DELETE FROM inbox; --",
    )
    assert store.ingest("binding", unusual, now_ms=2_000) == "queued"
    assert store.claim_ready("binding", now_ms=6_000).messages == (unusual,)
    assert store.confirm_rule("binding", "still works") == 2


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.set_enabled("missing", True, now_ms=0),
        lambda s: s.confirm_rule("missing", "rule"),
        lambda s: s.ingest("missing", message(), now_ms=2_000),
        lambda s: s.claim_ready("missing", now_ms=2_000),
        lambda s: s.set_takeover("missing", "chat", True, now_ms=2_000),
        lambda s: s.get_turn("binding", "missing"),
        lambda s: s.is_current("binding", "missing"),
        lambda s: s.finish_turn("binding", "missing", "completed"),
    ],
)
def test_missing_identity_is_not_silently_created(store, operation):
    with pytest.raises(KeyError):
        operation(store)


@pytest.mark.parametrize("text", ["", "  ", "x" * 16_001])
def test_invalid_confirmed_rules_rejected(store, text):
    with pytest.raises(ValueError):
        store.confirm_rule("binding", text)


@pytest.mark.parametrize("now", [-1, True, 0.5])
def test_invalid_clock_rejected(store, now):
    with pytest.raises(ValueError):
        store.claim_ready("binding", now_ms=now)


def test_invalid_conversation_and_outcome_rejected(store):
    with pytest.raises(ValueError):
        store.set_takeover("binding", "", True, now_ms=1_000)
    with pytest.raises(ValueError):
        store.finish_turn("binding", "missing", "retry")


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_control_flags_cannot_be_coerced(store, value):
    with pytest.raises(ValueError, match="boolean"):
        store.set_enabled("binding", value, now_ms=2_000)
    with pytest.raises(ValueError, match="boolean"):
        store.set_takeover("binding", "chat", value, now_ms=2_000)
    assert store.ingest("binding", message(), now_ms=2_000) == "queued"


def test_completed_turn_cannot_reopen(store):
    store.ingest("binding", message(), now_ms=2_000)
    turn = store.claim_ready("binding", now_ms=6_000)
    store.finish_turn("binding", turn.turn_id, "completed")
    store.finish_turn("binding", turn.turn_id, "completed")
    with pytest.raises(ValueError, match="cannot be reopened"):
        store.finish_turn("binding", turn.turn_id, "unknown")
    assert not store.is_current("binding", turn.turn_id)


def test_models_are_strict_and_batch_limits_validated():
    with pytest.raises(ValidationError):
        BatchPolicy(quiet_ms=16_000)
    with pytest.raises(ValidationError):
        BatchPolicy(max_messages=0)
    with pytest.raises(ValidationError):
        message(created="1000")
    with pytest.raises(ValidationError):
        message(text="x" * 16_001)
    with pytest.raises(ValidationError):
        message().text = "mutated"


def test_private_storage_and_delete_journal(state_dir):
    SqliteHostingStateStore(state_dir)
    path = state_dir / "dws_hosting.sqlite3"
    assert state_dir.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("PRAGMA application_id").fetchone()[0] == 0x44575348


def test_rejects_shared_directory(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        SqliteHostingStateStore(shared)


@pytest.mark.parametrize(
    "unsafe", ["permissions", "symlink", "directory", "version", "wal"]
)
def test_rejects_unsafe_or_incompatible_database(state_dir, tmp_path, unsafe):
    state_dir.mkdir(mode=0o700)
    path = state_dir / "dws_hosting.sqlite3"
    if unsafe == "symlink":
        path.symlink_to(tmp_path / "other")
    elif unsafe == "directory":
        path.mkdir(mode=0o700)
    else:
        path.touch(mode=0o600)
        if unsafe == "permissions":
            path.chmod(0o644)
        elif unsafe == "version":
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA user_version = 99")
        else:
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA journal_mode = WAL")
    with pytest.raises(ValueError):
        SqliteHostingStateStore(state_dir)


def test_rejects_symlink_state_directory(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    with pytest.raises(ValueError):
        SqliteHostingStateStore(alias)


@pytest.mark.parametrize("version", [0, 1])
def test_unrelated_database_is_not_adopted(state_dir, version):
    state_dir.mkdir(mode=0o700)
    path = state_dir / "dws_hosting.sqlite3"
    path.touch(mode=0o600)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
        if version:
            db.execute("PRAGMA user_version = 1")
    with pytest.raises(ValueError):
        SqliteHostingStateStore(state_dir)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == [("unrelated",)]


def test_concurrent_initialization_is_idempotent(state_dir):
    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(lambda _: SqliteHostingStateStore(state_dir), range(8)))
    for store in stores:
        store.bind(identity())
