"""Core fault injection through ports with real durable state."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from engine.community.core.dws_hosting.connection import DwsIntake, HostingAuthorization
from engine.community.kernel.dws_connection import (
    AuthorizationChallenge,
    AuthorizationStatus,
    ConnectionHealth,
    DwsError,
    HistoryResult,
    IntakePolicy,
    ListenerNotice,
)
from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore

from .test_state_contract import identity, message


class Connection:
    def __init__(self):
        self.status = AuthorizationStatus(
            authenticated=True, organization_id="org", user_id="self"
        )
        self.health_value = ConnectionHealth(connected=True, generation="1")
        self.result = HistoryResult(messages=(message(),), observed_ids=("m1",))
        self.discovered = ("chat",)
        self.calls = []
        self.listener_attempts = 0
        self.listener_closed = 0
        self.failures = []
        self.notices = asyncio.Queue()
        self.after_history = None
        self.error = None
        self.locked = False

    @asynccontextmanager
    async def exclusive(self):
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    async def authorization_status(self):
        return self.status

    async def authorize(self, callback):
        await callback(
            AuthorizationChallenge(
                verification_url="https://example.invalid", user_code="test"
            )
        )
        return self.status

    async def health(self):
        return self.health_value

    async def discover(self, start, end):
        if self.error:
            raise self.error
        return self.discovered

    async def history(self, cid, start, end):
        self.calls.append((cid, start, end))
        if self.after_history:
            self.after_history()
        return self.result

    async def listen(self):
        self.listener_attempts += 1
        try:
            if self.failures:
                raise DwsError(self.failures.pop(0))
            yield ListenerNotice(kind="ready")
            while True:
                yield await self.notices.get()
        finally:
            self.listener_closed += 1


@pytest.fixture
def setup(tmp_path):
    state = SqliteHostingStateStore(tmp_path / "state")
    state.bind(identity())
    state.confirm_rule("binding", "rule")
    state.set_enabled("binding", True, now_ms=1000)
    recovery = SqliteHostingRecoveryStore(tmp_path / "state")
    client = Connection()
    intake = DwsIntake(
        identity(),
        client,
        state,
        recovery,
        clock_ms=lambda: 5000,
        policy=IntakePolicy(
            poll_seconds=0.01,
            ready_timeout_seconds=0.05,
            retry_seconds=0.01,
            max_retry_seconds=0.02,
        ),
    )
    return state, recovery, client, intake


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_noop_reconcile_does_not_close_completed_claim_barrier(setup):
    state, _, _, intake = setup
    assert await intake.reconcile_once()
    assert not await intake.reconcile_once()
    assert state.claim_ready("binding", now_ms=9000) is not None


async def test_authorization_owner_only_and_never_enables(tmp_path):
    state = SqliteHostingStateStore(tmp_path / "state")
    client = Connection()
    auth = HostingAuthorization(identity(), client, state)
    seen = []

    async def challenge(value):
        seen.append(value)

    with pytest.raises(PermissionError):
        await auth.status("stranger")
    with pytest.raises(PermissionError):
        await auth.authorize("stranger", challenge)
    assert (await auth.status(identity().owner_id)).authenticated
    assert (await auth.authorize(identity().owner_id, challenge)).authenticated
    assert len(seen) == 1
    assert not client.locked
    assert state.ingest("binding", message(), now_ms=2000) == "ignored"
    client.status = AuthorizationStatus(authenticated=False)
    assert not (await auth.status(identity().owner_id)).authenticated
    with pytest.raises(DwsError, match="authorization_required"):
        await auth.authorize(identity().owner_id, challenge)


async def test_reconcile_deduplicates_and_restores_claim_barrier(setup):
    state, _, _, intake = setup
    assert await intake.reconcile_once()
    intake.clock_ms = lambda: 6000
    assert await intake.reconcile_once()
    turn = state.claim_ready("binding", now_ms=10_000)
    assert turn.messages == (message(),)


async def test_recovery_checks_known_conversations_even_when_discovery_empty(setup):
    _, recovery, client, intake = setup
    recovery.remember_conversation("binding", "chat")
    client.discovered = ()
    assert await intake.reconcile_once()
    assert client.calls == [("chat", 1000, 5000)]


async def test_notification_waits_for_eventual_history_visibility(setup):
    state, recovery, client, intake = setup
    recovery.record_hint(
        "binding",
        ListenerNotice(
            kind="conversation",
            conversation_id="chat",
            message_id="m1",
            created_at_ms=1100,
        ),
    )
    client.result = HistoryResult(messages=())
    assert await intake.reconcile_once()
    assert state.claim_ready("binding", now_ms=10_000) is None
    client.result = HistoryResult(messages=(message(),), observed_ids=("m1",))
    await intake.hints_once()
    assert state.claim_ready("binding", now_ms=10_000) is not None


async def test_partial_query_and_reconnect_never_release_claims(setup):
    state, _, client, intake = setup
    client.error = DwsError("incomplete_discovery")
    with pytest.raises(DwsError, match="incomplete_discovery"):
        await intake.reconcile_once()
    client.error = None
    client.after_history = lambda: setattr(
        client, "health_value", ConnectionHealth(connected=True, generation="2")
    )
    with pytest.raises(DwsError, match="connection_changed"):
        await intake.reconcile_once()
    assert state.claim_ready("binding", now_ms=10_000) is None
    client.after_history = None
    assert await intake.reconcile_once()
    assert state.claim_ready("binding", now_ms=10_000) is not None


@pytest.mark.parametrize(
    "status,code",
    [
        (AuthorizationStatus(authenticated=False), "authorization_required"),
        (
            AuthorizationStatus(
                authenticated=True, organization_id="org", user_id="other"
            ),
            "identity_mismatch",
        ),
    ],
)
async def test_wrong_or_expired_identity_stops_without_consuming(setup, status, code):
    _, _, client, intake = setup
    client.status = status
    with pytest.raises(DwsError, match=code):
        await intake.run()
    assert client.listener_attempts == 0
    assert not client.locked


async def test_disconnected_and_foreign_history_rejected(setup):
    _, _, client, intake = setup
    client.health_value = ConnectionHealth(connected=False, generation="1")
    with pytest.raises(DwsError, match="disconnected"):
        await intake.reconcile_once()
    client.health_value = ConnectionHealth(connected=True, generation="1")
    client.result = HistoryResult(messages=(message(chat="foreign"),))
    with pytest.raises(DwsError, match="history_scope_mismatch"):
        await intake.reconcile_once()


async def test_supervisor_restarts_failed_consumer_and_closes_on_cancel(setup):
    state, _, client, intake = setup
    client.failures = ["listener_ended", "command_timeout"]
    task = asyncio.create_task(intake.run())
    await wait_until(lambda: bool(client.calls))
    assert client.listener_attempts == 3
    await client.notices.put(
        ListenerNotice(
            kind="conversation",
            conversation_id="chat",
            message_id="late",
            created_at_ms=1200,
        )
    )
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.listener_closed == 3
    assert not client.locked
    assert state.claim_ready("binding", now_ms=10_000) is None


async def test_invalid_event_stops_instead_of_retrying_forever(setup):
    _, _, client, intake = setup
    client.failures = ["unexpected_event"]
    with pytest.raises(DwsError, match="unexpected_event"):
        await intake.run()
    assert client.listener_attempts == 1


async def test_ready_timeout_ends_cycle_and_cleans_listener(setup):
    _, _, client, intake = setup

    async def silent():
        await asyncio.sleep(60)
        yield ListenerNotice(kind="ready")

    client.listen = silent
    with pytest.raises(DwsError, match="listener_not_ready"):
        await intake._cycle()


async def test_finished_iterator_is_a_failure(setup):
    _, _, client, intake = setup

    async def ended():
        yield ListenerNotice(kind="ready")

    client.listen = ended
    with pytest.raises(DwsError, match="listener_ended"):
        await intake._cycle()


async def test_no_elapsed_interval_does_not_query_history(setup):
    _, _, client, intake = setup
    intake.clock_ms = lambda: 1000
    assert not await intake.reconcile_once()
    assert client.calls == []


async def test_periodic_recovery_without_any_listener_message(setup):
    _, _, client, intake = setup
    ticks = iter(range(5000, 100_000, 1000))
    intake.clock_ms = lambda: next(ticks)
    task = asyncio.create_task(intake.run())
    await wait_until(lambda: len(client.calls) >= 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.listener_closed == 1


async def test_transient_auth_status_failure_is_retried(setup):
    _, _, client, intake = setup
    calls = 0

    async def status():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DwsError("command_timeout")
        return client.status

    client.authorization_status = status
    task = asyncio.create_task(intake.run())
    await wait_until(lambda: bool(client.calls))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls >= 3


async def test_wrong_persisted_binding_is_rejected(setup):
    _, recovery, _, intake = setup
    original = recovery.recovery_window

    def wrong(*args, **kwargs):
        return original(*args, **kwargs).model_copy(
            update={"binding": identity(bot_id="other")}
        )

    recovery.recovery_window = wrong
    with pytest.raises(DwsError, match="identity_mismatch"):
        await intake.reconcile_once()
