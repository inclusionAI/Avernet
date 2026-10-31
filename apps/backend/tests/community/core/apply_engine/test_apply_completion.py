"""Every way an apply ends tells its state's producer: the completion publish (``completion.py``).

A stub compiler is registered under a test kind, so each test chooses how the
apply ends — a clean run, a refused compile, an engine exception, a task that
cannot be rebuilt, a given-up task — and the recording subscriber says what it
was told, and whether the apply's lock was still held when it was told.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.apply_engine.compiler import (
    Compiled,
    CompileRefused,
    DesiredStateCompiler,
    StateRef,
)
from agentclaw.community.core.apply_engine.completion import (
    ApplyCompletionHandler,
    ApplyOutcome,
    CompletionPublisher,
    outcome_of,
)
from agentclaw.community.core.apply_engine.desired_state import DesiredState
from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.outcomes import (
    ApplyReport,
    ApplyStatus,
    CategoryResult,
    EntryOutcome,
    EntryResult,
)

# Imported for side effect: registers the apply tables on Base.metadata.
from agentclaw.community.core.apply_engine.repository.apply_models import (  # noqa: F401
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyModel,
)
from agentclaw.community.core.apply_engine.run.service import ApplyRunService
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)
from agentclaw.community.utils.env_utils import get_current_env

from ._fakes import arca_only_delivery, device_port_bundle

_ENTITY = "u_owner"
_BOT = "b_1"
_BOT_RECORD = {
    "bot_id": _BOT,
    "owner_id": _ENTITY,
    "entity_id": _ENTITY,
    "active_engine": "claude_code",
    "bot_type": "personal",
}
_STATE = StateRef("stub", "ref-1")


class _DB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _StubCompiler(DesiredStateCompiler):
    """Compiles to an empty state, or does what the test told it to instead."""

    def __init__(self) -> None:
        self.compiles = 0
        self.raise_on_compile: Exception | None = None

    def check(self, ctx, ref) -> None:
        return None

    async def compile(self, ctx, ref, *, only=None) -> Compiled:
        self.compiles += 1
        if self.raise_on_compile is not None:
            raise self.raise_on_compile
        return Compiled(DesiredState.EMPTY)


class _Recorder(ApplyCompletionHandler):
    """Records each notice and whether the bot's apply lock was held at that moment."""

    def __init__(self, locks) -> None:
        self._locks = locks
        self.notices: list[tuple[StateRef, ApplyOutcome, bool]] = []
        self.raise_next: Exception | None = None

    def on_finished(self, *, env, entity_id, bot_id, state, outcome) -> None:
        held = self._locks.get(env=env, entity_id=entity_id, bot_id=bot_id) is not None
        self.notices.append((state, outcome, held))
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error


class _Queue:
    """Records payloads; each test runs the task itself, the way a worker would."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.payloads.append(payload)
        return (None, True)


class _Bots:
    def __init__(self, record):
        self.record = record

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.record


class _Capabilities:
    def capabilities_for_bot(self, bot):
        from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities

        return resolve_capabilities(
            active_engine=bot.get("active_engine"), bot_type=bot.get("bot_type"), is_teclaw=lambda e: e == "teclaw"
        )


@pytest.fixture
def rig():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    db = _DB(engine)
    applies = BotConfigManifestApplyRepository(db)
    locks = BotConfigManifestApplyLockRepository(db)
    compiler = _StubCompiler()
    recorder = _Recorder(locks)
    queue = _Queue()
    bots = _Bots(_BOT_RECORD)
    service = ApplyRunService(
        capabilities=_Capabilities(),
        apply_repository=applies,
        lock_repository=locks,
        task_queue_provider=lambda: queue,
        bot_repository=bots,
        delivery_strategies=arca_only_delivery(device_port_bundle()),
        compilers={"stub": compiler},
        completion_handlers={"stub": recorder},
    )
    return service, compiler, recorder, queue, locks, bots


def _start(service, queue) -> dict:
    service.start_apply(
        entity_id=_ENTITY, bot_id=_BOT, bot=_BOT_RECORD, owner_id=_ENTITY, actor_id=_ENTITY, state=_STATE
    )
    return queue.payloads[-1]


def _lock_held(locks) -> bool:
    return locks.get(env=get_current_env(), entity_id=_ENTITY, bot_id=_BOT) is not None


def test_a_finished_apply_is_published_once_before_its_lock_is_released(rig) -> None:
    service, _, recorder, queue, locks, _ = rig
    service.run_apply_task(_start(service, queue))
    [(state, outcome, held)] = recorder.notices
    assert state == _STATE
    assert outcome.status is ApplyStatus.SUCCEEDED and outcome.summary == "applied"
    assert held, "the subscriber hears before the next apply can take the lock"
    assert not _lock_held(locks)


@pytest.mark.parametrize(
    ("error", "summary"),
    [
        (
            CompileRefused("promotion p is not there", domain_error=ValueError("p")),
            "compile refused: promotion p is not there",
        ),
        (RuntimeError("boom"), "the apply failed"),
    ],
)
def test_a_refused_compile_and_an_engine_exception_are_published_as_failed(rig, error, summary) -> None:
    service, compiler, recorder, queue, locks, _ = rig
    compiler.raise_on_compile = error
    service.run_apply_task(_start(service, queue))
    [(_, outcome, held)] = recorder.notices
    assert (outcome.status, outcome.summary, held) == (ApplyStatus.FAILED, summary, True)
    assert not _lock_held(locks)


def test_a_task_that_cannot_be_rebuilt_is_published_as_failed(rig) -> None:
    service, compiler, recorder, queue, locks, bots = rig
    payload = _start(service, queue)
    bots.record = None  # the bot went away between the enqueue and the run
    service.run_apply_task(payload)
    [(_, outcome, held)] = recorder.notices
    assert (outcome.status, held, compiler.compiles) == (ApplyStatus.FAILED, True, 0)
    assert not _lock_held(locks)


def test_an_unstartable_apply_whose_publish_fails_keeps_the_bot_locked_for_the_rerun(rig) -> None:
    service, compiler, recorder, queue, locks, bots = rig
    payload = _start(service, queue)
    bots.record = None
    recorder.raise_next = RuntimeError("database blip")
    with pytest.raises(RuntimeError):
        service.run_apply_task(payload)
    assert _lock_held(locks)
    service.run_apply_task(payload)  # the re-run finds the FAILED report, publishes, unlocks
    assert not _lock_held(locks) and compiler.compiles == 0


def test_a_failed_publish_is_retried_by_the_queue_and_the_rerun_only_publishes(rig) -> None:
    service, compiler, recorder, queue, locks, _ = rig
    payload = _start(service, queue)
    recorder.raise_next = RuntimeError("database blip")
    with pytest.raises(RuntimeError, match="database blip"):
        service.run_apply_task(payload)  # the worker turns this raise into a retry
    assert _lock_held(locks), "the bot stays locked until the subscriber has heard"
    service.run_apply_task(payload)  # the retry
    assert compiler.compiles == 1, "the re-run applies nothing again"
    assert [n[1].status for n in recorder.notices] == [ApplyStatus.SUCCEEDED, ApplyStatus.SUCCEEDED]
    assert not _lock_held(locks), "a publish that lands unlocks"


def test_a_publish_that_fails_again_on_the_rerun_keeps_the_bot_locked_until_the_queue_gives_up(rig) -> None:
    service, _, recorder, queue, locks, _ = rig
    payload = _start(service, queue)
    recorder.raise_next = RuntimeError("database blip")
    with pytest.raises(RuntimeError):
        service.run_apply_task(payload)
    recorder.raise_next = RuntimeError("still down")
    with pytest.raises(RuntimeError):
        service.run_apply_task(payload)
    assert _lock_held(locks), "a second promotion cannot start while the first is unsettled"
    service.give_up(payload)  # retries exhausted or deadline: the queue gives up, nothing will retry
    assert not _lock_held(locks)


def test_a_rerun_after_a_worker_died_before_unlocking_releases_the_lock(rig, monkeypatch) -> None:
    service, compiler, recorder, queue, locks, _ = rig
    payload = _start(service, queue)
    with monkeypatch.context() as died:
        died.setattr(locks, "release", lambda **_: False)  # the worker dies between the terminal write and the release
        service.run_apply_task(payload)
    assert _lock_held(locks)
    service.run_apply_task(payload)  # the queue re-claims the task
    assert not _lock_held(locks), "the bot is free again without waiting out the lock's TTL"
    assert compiler.compiles == 1 and [n[1].status for n in recorder.notices] == [ApplyStatus.SUCCEEDED] * 2


def test_a_rerun_of_an_ended_apply_leaves_the_next_apply_s_lock_alone(rig) -> None:
    service, _, _, queue, locks, _ = rig
    first = _start(service, queue)
    service.run_apply_task(first)
    _start(service, queue)  # the next apply takes the lock
    service.run_apply_task(first)
    assert _lock_held(locks), "the release is by token"


def test_a_given_up_apply_is_finished_failed_unlocked_and_published(rig) -> None:
    service, compiler, recorder, queue, locks, _ = rig
    payload = _start(service, queue)  # no run ever happens: the queue gives up on the task
    service.give_up(payload)
    report = service.get_apply(entity_id=_ENTITY, bot_id=_BOT, apply_id=payload["apply_id"])
    assert report.status is ApplyStatus.FAILED
    assert report.notes == ("the apply task was given up before any run finished (deadline elapsed)",)
    [(_, outcome, held)] = recorder.notices
    assert (outcome.status, outcome.summary) == (ApplyStatus.FAILED, report.notes[0])
    assert held and not _lock_held(locks) and compiler.compiles == 0


def test_giving_up_on_an_apply_that_already_ended_republishes_its_report_unchanged(rig) -> None:
    service, _, recorder, queue, locks, _ = rig
    payload = _start(service, queue)
    service.run_apply_task(payload)
    service.give_up(payload)
    report = service.get_apply(entity_id=_ENTITY, bot_id=_BOT, apply_id=payload["apply_id"])
    assert report.status is ApplyStatus.SUCCEEDED
    assert [n[1].status for n in recorder.notices] == [ApplyStatus.SUCCEEDED, ApplyStatus.SUCCEEDED]
    assert not _lock_held(locks)


def test_a_kind_without_a_subscriber_is_told_nothing(rig) -> None:
    service, _, recorder, queue, _, _ = rig
    service._completion = CompletionPublisher({})  # the Manifest's case: no row in the map
    service.run_apply_task(_start(service, queue))
    assert recorder.notices == []


def _report(status: ApplyStatus, *, categories=(), notes=()) -> ApplyReport:
    return ApplyReport(
        apply_id="a1",
        bot_id=_BOT,
        trigger="explicit",
        status=status,
        started_at=datetime(2026, 10, 28),
        categories=categories,
        notes=notes,
    )


def test_outcome_of_names_what_a_partial_apply_did_not_deliver() -> None:
    entries = tuple(
        EntryResult(Category.SKILLS, name, outcome)
        for name, outcome in [
            ("ok", EntryOutcome.CREATED),
            ("a", EntryOutcome.FAILED),
            ("b", EntryOutcome.SKIPPED),
            ("c", EntryOutcome.FAILED),
            ("d", EntryOutcome.FAILED),
        ]
    )
    outcome = outcome_of(_report(ApplyStatus.PARTIAL, categories=(CategoryResult(Category.SKILLS, entries),)))
    skills = Category.SKILLS.value
    assert outcome.summary == (
        f"4 entries not delivered: {skills}/a (failed), {skills}/b (skipped), {skills}/c (failed) and 1 more"
    )


def test_outcome_of_a_failed_apply_is_its_first_note_and_never_a_running_one() -> None:
    assert outcome_of(_report(ApplyStatus.FAILED, notes=("compile refused: x",))).summary == "compile refused: x"
    assert outcome_of(_report(ApplyStatus.FAILED)).summary == "the apply failed"
    with pytest.raises(ValueError, match="terminal"):
        outcome_of(_report(ApplyStatus.RUNNING))


def test_the_apply_task_handler_hands_a_given_up_task_to_the_run_service(rig) -> None:
    from agentclaw.community.core.apply_engine.apply_task import ApplyTaskHandler
    from agentclaw.community.core.task_queue.services.registry import GivesUp
    from agentclaw.community.core.task_queue.types import TaskRecord, TaskStatus

    service, _, recorder, queue, locks, _ = rig
    payload = _start(service, queue)
    handler = ApplyTaskHandler(lambda: service)
    assert isinstance(handler, GivesUp)
    handler.on_give_up(
        TaskRecord(
            id=1,
            task_type=handler.task_type,
            payload=payload,
            status=TaskStatus.TIMED_OUT,
            deadline_at=datetime(2026, 10, 28),
            run_at=None,
            claimed_by=None,
            lease_expires_at=None,
            attempts=0,
            last_error=None,
            env=get_current_env(),
            app="agentclaw",
        )
    )
    assert [n[1].status for n in recorder.notices] == [ApplyStatus.FAILED]
    assert not _lock_held(locks)
