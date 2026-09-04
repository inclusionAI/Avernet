"""The service's lifecycle: serialization, the two writes, and the stale case.

These are the criteria the orchestrator tests cannot reach, because they are
about what happens *around* the engine — the lock, the record's two writes, and
what a poller sees. Run against in-memory SQLite through the real repository
bodies, so the UNIQUE-constraint-as-lock is exercised for real rather than
mocked.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    ApplyStatus,
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.apply.order import (
    ALL_PHASES,
    ApplyPhase,
)
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    ManifestApplyInProgressError,
)

# Imported for side effect: registers the models on Base.metadata so
# create_all() builds both tables.
from agentclaw.community.core.bot_config_manifest.repository.apply_models import (  # noqa: F401
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyModel,
)
from agentclaw.community.core.bot_config_manifest.repository.models import (  # noqa: F401
    BotConfigManifestModel,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_apply_service import (
    APPLY_LOCK_TTL_SECONDS,
    BotConfigManifestApplyService,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest import (
    BotConfigManifestRepository,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeGitClient,
    FakeGuardedFetcher,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeResourceFileService,
    FakeSkillUploadService,
    FakeStartupScriptService,
    real_validator,
)

_ENTITY = "u_owner"
_BOT = "b_1"
_DOCUMENT = 'schema_version: 1\nscript:\n  body: "echo hello"\n'
_BOT_RECORD = {
    "bot_id": _BOT,
    "owner_id": _ENTITY,
    "entity_id": _ENTITY,
    "active_engine": "claude_code",
    "bot_type": "personal",
}


class InMemorySqliteDB:
    def __init__(self, engine):
        self._engine = engine
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _ManifestService:
    """The document half, reduced to what apply asks of it."""

    def __init__(self, document: str | None) -> None:
        self._document = document

    def get(self, *, entity_id, bot_id):
        if self._document is None:
            return None
        return type("R", (), {"document": self._document})()

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document)})()

    def capabilities_for_bot(self, bot):
        from agentclaw.community.core.bot_config_manifest.capabilities import (
            resolve_capabilities,
        )

        return resolve_capabilities(
            active_engine=bot.get("active_engine"),
            bot_type=bot.get("bot_type"),
            is_teclaw=lambda engine: engine == "teclaw",
        )


class _FakeBotRepository:
    """Just the one lookup ``run_apply_task`` makes to rebuild its context."""

    def __init__(self, record: dict | None) -> None:
        self._record = record

    def get_by_id_and_entity(self, bot_id: str, entity_id: str):
        return self._record


class _FakeTaskQueue:
    """A worker that claims immediately, which is what these tests need.

    Applying moved off a daemon thread onto the task queue, so the service now
    *enqueues* rather than runs. These tests are about what happens **around** the
    engine — the lock, the two writes, what a poller sees — and all of that still
    happens, just on a worker. Running the handler inline on enqueue keeps every
    assertion below testing the same lifecycle rather than testing the queue.

    It is also a faithful stand-in: the real type is registered with
    ``wake_on_enqueue=True`` precisely so a due apply is claimed at once instead
    of waiting out an idle poll.
    """

    def __init__(self) -> None:
        self.service = None
        self.enqueued: list[tuple[str, dict]] = []

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        self.enqueued.append((task_type, payload))
        if self.service is not None:
            self.service.run_apply_task(payload)
        return (None, True)


@pytest.fixture
def world():
    # StaticPool, and it is load-bearing rather than incidental: apply does its
    # work on a background thread, and SQLite's default pooling hands each
    # thread its *own* ``:memory:`` database — so the worker would find an empty
    # one and fail with "no such table". One shared connection is what lets the
    # thread see the same database the fixture built.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    db = InMemorySqliteDB(engine)

    queue = _FakeTaskQueue()
    scripts = FakeStartupScriptService()
    applies = BotConfigManifestApplyRepository(db)
    locks = BotConfigManifestApplyLockRepository(db)
    service = BotConfigManifestApplyService(
        manifest_service=_ManifestService(_DOCUMENT),
        apply_repository=applies,
        lock_repository=locks,
        script_service_provider=lambda: scripts,
        activation_service_provider=lambda: FakeActivationService(),
        mcp_auth_service_provider=lambda: FakeMcpAuth(),
        # W5's materialisers. This suite's document declares only mcp and
        # script, so the fetch-consuming categories' services are never
        # reached — but they must exist for the registry to register.
        identity_service_provider=lambda: FakeIdentityService(),
        upload_service_provider=lambda: FakeSkillUploadService(),
        capability_reader_provider=lambda: FakeCapabilityReader(),
        package_validator_provider=lambda: real_validator(),
        entry_fetcher_provider=lambda: EntryFetcher(
            FakeGuardedFetcher(), FakeManifestContent(), FakeCredentials()
        ),
        # W6's materialiser: this suite's document declares no resources,
        # so the write chain is never reached — but it must exist for the
        # registry to register.
        resource_service_provider=lambda: FakeResourceFileService(),
        cli_tool_service_factory=lambda family: None,
        # W7's git transport. This suite's document declares no ``sources``,
        # so the client is never *used* — it is constructed per apply and
        # must never be fetched through, which FakeGitClient enforces.
        git_client_provider=lambda: FakeGitClient(),
        task_queue_provider=lambda: queue,
        bot_repository=_FakeBotRepository(_BOT_RECORD),
    )
    # Closes the loop: the fake worker needs the service it runs work for.
    queue.service = service
    return service, applies, locks, scripts, BotConfigManifestRepository(db)


def _start(service):
    return service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        phases=ALL_PHASES,
    )


def _drain(service):
    """Let the background thread finish, then read the terminal report.

    Joins the worker rather than only polling the record. The terminal write is
    not the thread's last act — the lock release follows it — so a test that
    stopped at "the report is terminal" could observe the lock still held, and
    could tear the engine down while the worker was still on a connection.
    Joining removes both windows: once ``join`` returns, the thread is done
    touching anything.
    """
    import threading
    import time

    for thread in threading.enumerate():
        if thread.name.startswith("manifest-apply-"):
            thread.join(timeout=30)
            assert not thread.is_alive(), "the apply thread never finished"

    for _ in range(200):
        report = service.last_apply(entity_id=_ENTITY, bot_id=_BOT)
        if report is not None and report.status is not ApplyStatus.RUNNING:
            return report
        time.sleep(0.01)
    raise AssertionError("the apply never reached a terminal status")


def _counting_session_closes(monkeypatch) -> list[SourceSession]:
    """Wrap ``SourceSession.close`` so a test can assert it ran.

    Delegates to the real close — the point is *that* the terminal path calls
    it, not what it does (its own file tests the removal). Every terminal
    path of an apply must close its session: the checkouts live in
    ``mkdtemp`` trees and nothing outside the session ever names them again.
    """
    closed: list[SourceSession] = []
    original = SourceSession.close

    def _close(self) -> None:
        closed.append(self)
        original(self)

    monkeypatch.setattr(SourceSession, "close", _close)
    return closed


def test_start_apply_returns_a_handle_and_the_work_finishes(world):
    """The whole async shape, end to end: 202-worthy answer, then a terminal report."""
    service, _applies, _locks, scripts, _manifests = world

    accepted = _start(service)
    assert accepted.apply_id
    assert accepted.status is ApplyStatus.RUNNING

    report = _drain(service)
    assert report.status is ApplyStatus.SUCCEEDED
    assert report.apply_id == accepted.apply_id
    assert report.finished_at is not None
    assert scripts.body == "echo hello"


def test_two_applies_serialize_and_the_second_is_refused_before_an_id(world):
    """One proceeds; the other is refused, and refused *before* minting an id.

    A caller who receives an ``apply_id`` must be able to trust that an apply
    with that id started. Raising after the id existed would leave a handle
    pointing at nothing.
    """
    service, applies, locks, _scripts, _manifests = world

    # Hold the lock as if an apply were in flight.
    held = locks.acquire(
        env="dev", entity_id=_ENTITY, bot_id=_BOT, holder_user_id="someone-else"
    )
    assert held is not None

    with pytest.raises(ManifestApplyInProgressError):
        _start(service)

    # Nothing was recorded: the refusal happened before the row was written.
    assert applies.latest(env="dev", entity_id=_ENTITY, bot_id=_BOT) is None


def test_the_lock_is_released_so_a_later_apply_can_run(world):
    """A finished apply must not leave the bot locked against every future one."""
    service, _applies, locks, _scripts, _manifests = world

    _start(service)
    _drain(service)

    assert locks.get(env="dev", entity_id=_ENTITY, bot_id=_BOT) is None
    second = _start(service)
    assert second.apply_id
    _drain(service)


def test_dry_run_writes_no_row_and_mints_no_id(world):
    """"Writes nothing" covers the record as well as the bot.

    Counted rather than trusted: the table is read before and after, so a future
    change that started recording plans fails here.
    """
    import asyncio

    service, applies, _locks, scripts, _manifests = world

    before = applies.latest(env="dev", entity_id=_ENTITY, bot_id=_BOT)
    report = asyncio.run(
        service.dry_run(
            entity_id=_ENTITY,
            bot_id=_BOT,
            bot=_BOT_RECORD,
            owner_id=_ENTITY,
            actor_id=_ENTITY,
        )
    )

    assert report.apply_id == ""
    assert applies.latest(env="dev", entity_id=_ENTITY, bot_id=_BOT) is before
    assert scripts.writes == 0


def test_a_running_report_with_no_live_lock_reads_as_failed(world):
    """A process killed mid-apply must not strand a poller forever.

    The row stays ``RUNNING`` because the ``finally`` never ran. With no lock
    behind it, no apply can still be working, so the read derives ``FAILED`` —
    at read time, with no sweeper to keep alive.
    """
    service, applies, locks, _scripts, _manifests = world

    applies.start(
        env="dev",
        entity_id=_ENTITY,
        bot_id=_BOT,
        apply_id="abandoned",
        trigger="explicit",
        actor=_ENTITY,
        report="{}",
    )
    assert locks.get(env="dev", entity_id=_ENTITY, bot_id=_BOT) is None

    report = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id="abandoned"
    )
    assert report is not None
    assert report.status is ApplyStatus.FAILED


def test_a_running_report_with_a_live_lock_still_reads_as_running(world):
    """The counterpart: an apply genuinely in flight must not be called failed.

    Without this, the staleness rule above would be indistinguishable from
    "always report FAILED", and a poller would never see a real apply working.
    """
    service, applies, locks, _scripts, _manifests = world

    applies.start(
        env="dev",
        entity_id=_ENTITY,
        bot_id=_BOT,
        apply_id="in-flight",
        trigger="explicit",
        actor=_ENTITY,
        report="{}",
    )
    locks.acquire(
        env="dev", entity_id=_ENTITY, bot_id=_BOT, holder_user_id=_ENTITY
    )

    report = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id="in-flight"
    )
    assert report is not None
    assert report.status is ApplyStatus.RUNNING


def test_an_apply_id_from_another_bot_is_not_found(world):
    """The id is a handle, never what authorizes the read."""
    service, _applies, _locks, _scripts, _manifests = world

    accepted = _start(service)
    _drain(service)

    assert (
        service.get_apply(
            entity_id=_ENTITY, bot_id="a-different-bot", apply_id=accepted.apply_id
        )
        is None
    )


def test_a_bot_that_never_applied_reads_as_none(world):
    """``None``, never an error — the rule the manifest's own read follows."""
    service, _applies, _locks, _scripts, _manifests = world
    assert service.last_apply(entity_id=_ENTITY, bot_id="never-applied") is None


def test_the_lock_ttl_is_long_enough_to_be_a_safety_net(world):
    """A guard against someone "tidying" the TTL down to a timeout.

    It bounds an *abandoned* apply, not a slow one. Set it near an apply's real
    duration and a legitimately long apply (W5 fetching several sources) has its
    lock stolen mid-write.
    """
    assert APPLY_LOCK_TTL_SECONDS >= 15 * 60


def test_the_audit_label_is_recorded_without_becoming_the_principal(world):
    """Review finding: the two actors are different things.

    ``audit_actor`` is a label — for an application caller a synthetic
    ``app:<id>:on-behalf-of:<user>`` that matches no owner or collaborator row.
    It belongs in the record's actor column. It was also being passed on as the
    operational ``actor_id``, which is what every downstream authorization check
    is made against (``can_manage_bot``, ``check_mcp_permission_detail``), so
    every application caller was denied.

    Asserted on both sides at once: the label reaches the record, and the
    principal — not the label — reaches the work.
    """
    service, applies, _locks, scripts, _manifests = world
    label = "app:the-app:on-behalf-of:u_owner"

    service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        audit_actor=label,
        phases=ALL_PHASES,
    )
    report = _drain(service)

    assert report.status is ApplyStatus.SUCCEEDED
    row = applies.latest(env="dev", entity_id=_ENTITY, bot_id=_BOT)
    assert row.actor == label, "the audit column lost the application label"
    # The work ran as the principal: had the label been passed through as the
    # actor, the script write below would have been attributed to a non-principal
    # — and on the mcp path it would have been refused outright.
    assert scripts.puts[0]["modifier"] == _ENTITY


def test_the_audit_label_defaults_to_the_principal(world):
    """A caller with nothing to distinguish keeps the obvious behaviour."""
    service, applies, _locks, _scripts, _manifests = world

    service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        phases=ALL_PHASES,
    )
    _drain(service)

    row = applies.latest(env="dev", entity_id=_ENTITY, bot_id=_BOT)
    assert row.actor == _ENTITY


def test_partially_written_survives_the_storage_round_trip(world):
    """Review finding: the flag was written but never read back.

    ``POST .../apply`` returns only a handle, so every caller sees its report
    through ``get_apply``/``last_apply`` — i.e. through the stored JSON. The
    reconstruction built ``CategoryResult`` without ``partially_written``, so it
    silently took the dataclass default ``False``: the one signal that an
    aborted category may already have changed the bot was dropped on the only
    path anybody uses. A field written by ``as_dict`` but not read back does not
    exist as far as the API is concerned.
    """
    import json

    service, applies, _locks, _scripts, _manifests = world
    applies.start(
        env="dev",
        entity_id=_ENTITY,
        bot_id=_BOT,
        apply_id="half-written",
        trigger="explicit",
        actor=_ENTITY,
        report="{}",
    )
    applies.finish(
        env="dev",
        entity_id=_ENTITY,
        bot_id=_BOT,
        apply_id="half-written",
        status="FAILED",
        report=json.dumps(
            {
                "apply_id": "half-written",
                "bot_id": _BOT,
                "trigger": "explicit",
                "result": "FAILED",
                "started_at": None,
                "finished_at": None,
                "sources": [],
                "categories": [
                    {
                        "category": "mcp",
                        "aborted": True,
                        "partially_written": True,
                        "removed": [],
                    }
                ],
                "entries": [],
            }
        ),
    )

    report = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id="half-written"
    )
    assert report is not None
    assert report.categories[0].aborted is True
    assert report.categories[0].partially_written is True, (
        "the poller was told the area is untouched when it may be half-written"
    )


def test_an_enqueue_that_fails_terminates_the_report_and_frees_the_lock(
    world, monkeypatch
):
    """The launch-failure window the audit found, on the queue: the RUNNING row
    is already written when the handoff raises, leaving the bot locked for the
    30-minute TTL while a poller waits on an apply that never existed. Thread
    exhaustion used to be the trigger; a queue write that cannot land is the
    same shape of failure and gets the same answer — FAILED row, lock released,
    caller hears the original error."""
    service, applies, locks, scripts, _ = world
    queue = service._task_queue_provider()
    original_enqueue = queue.enqueue

    class _QueueUnavailableError(RuntimeError):
        pass

    def _explode(*args, **kwargs):
        raise _QueueUnavailableError("could not reach the queue")

    monkeypatch.setattr(queue, "enqueue", _explode)

    with pytest.raises(_QueueUnavailableError):
        service.start_apply(
            entity_id=_ENTITY,
            bot_id=_BOT,
            bot=_BOT_RECORD,
            owner_id=_ENTITY,
            actor_id=_ENTITY,
            phases=ALL_PHASES,
        )

    # The report is terminal, not stranded RUNNING.
    report = service.last_apply(entity_id=_ENTITY, bot_id=_BOT)
    assert report is not None
    assert report.status is ApplyStatus.FAILED

    # And the bot is immediately re-applyable — no TTL wait.
    monkeypatch.setattr(queue, "enqueue", original_enqueue)
    accepted = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        phases=ALL_PHASES,
    )
    assert accepted.status is ApplyStatus.RUNNING


# ── W7: the per-apply source session ──────────────────────────────────────────


def test_a_source_session_is_built_per_apply_and_closed(world, monkeypatch):
    """The W7 wiring this suite can see without declaring git sources.

    One session per apply, observed at its only seam: the git client the
    provider builds for it. Two applies from one document therefore mean
    two constructions — a session shared across applies would leave one
    apply reading another's checkout cache, and a session built inside the
    orchestrator would mean the report's baselines and resolutions live on
    the wrong side of the work. And the session closes on the normal path:
    its checkouts are ``mkdtemp`` trees nothing else will ever name.
    """
    service, _applies, _locks, _scripts, _manifests = world
    closed = _counting_session_closes(monkeypatch)
    before = FakeGitClient.constructed

    _start(service)
    first = _drain(service)
    assert first.status is ApplyStatus.SUCCEEDED

    _start(service)
    second = _drain(service)
    assert second.status is ApplyStatus.SUCCEEDED

    assert FakeGitClient.constructed - before == 2, (
        "each apply must build its own source session (one git client each)"
    )
    assert len(closed) == 2, "every finished apply must close its session"


def _row(report: ApplyReport) -> object:
    """A duck-typed apply row carrying one report payload — every field
    ``_report_from_payload`` re-reads off the record (apply_id, bot_id,
    trigger, started_at, finished_at, report, status)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        apply_id=report.apply_id,
        bot_id=report.bot_id,
        trigger=report.trigger,
        started_at=report.started_at,
        finished_at=None,
        report=json.dumps(report.as_payload()),
        status=report.status.value,
    )


def _report_with_sources(
    resolutions, *, status=ApplyStatus.SUCCEEDED, apply_id: str = "prior"
) -> ApplyReport:
    return ApplyReport(
        apply_id=apply_id,
        bot_id=_BOT,
        trigger="explicit",
        status=status,
        started_at=datetime.now(),
        sources=tuple(resolutions),
    )


def test_a_strict_baseline_is_read_back_from_report_history(world, monkeypatch):
    """``_last_resolutions``: the reports ARE the baseline table.

    Strict mode reads "what did we resolve last time" out of
    ``ApplyReport.sources`` rather than a second table, so the two cannot
    drift. No report — and a report with no resolutions — yield no
    opinions; a recorded resolution yields its SHA by name; and the walk is
    bounded by the history window rather than one row (see the next test).
    """
    service, _applies, _locks, _scripts, _manifests = world

    # Never applied: no report, no baselines.
    assert service._last_resolutions(entity_id=_ENTITY, bot_id=_BOT) == {}

    # A real apply with no declared sources records no resolutions.
    _start(service)
    report = _drain(service)
    assert report.status is ApplyStatus.SUCCEEDED
    assert report.sources == ()
    assert service._last_resolutions(entity_id=_ENTITY, bot_id=_BOT) == {}

    charts = SourceResolution(
        name="charts", ref="main", resolved_sha="f" * 40, auth="ci-token"
    )
    monkeypatch.setattr(
        service._applies,
        "recent",
        lambda *, env, entity_id, bot_id, limit: [_row(
            _report_with_sources([charts])
        )],
    )
    assert service._last_resolutions(
        entity_id=_ENTITY, bot_id=_BOT
    ) == {"charts": "f" * 40}


def test_a_failed_apply_does_not_wipe_a_strict_baseline(world, monkeypatch):
    """The walk-back is the fix the review required: only the newest row
    reads as "the last apply", and a newest apply whose fetch failed (its
    report carries no resolution for the source) must not disarm strict
    mode for the apply after it — the record one row back still holds the
    baseline, and the newest row that carries a source wins per source."""
    service, _applies, _locks, _scripts, _manifests = world
    charts = SourceResolution(name="charts", ref="main", resolved_sha="e" * 40)
    empty_failed = _report_with_sources(
        [], status=ApplyStatus.FAILED, apply_id="failed-1"
    )
    succeeded = _report_with_sources([charts], apply_id="prior-1")

    # Newest first: the failed fetch recorded nothing; the row before it did.
    monkeypatch.setattr(
        service._applies,
        "recent",
        lambda *, env, entity_id, bot_id, limit: [_row(empty_failed), _row(succeeded)],
    )
    assert service._last_resolutions(
        entity_id=_ENTITY, bot_id=_BOT
    ) == {"charts": "e" * 40}

    # Newest wins per source: a newer report that re-resolved the source is
    # the baseline, not an older one.
    moved = SourceResolution(name="charts", ref="main", resolved_sha="b" * 40)
    monkeypatch.setattr(
        service._applies,
        "recent",
        lambda *, env, entity_id, bot_id, limit: [
            _row(_report_with_sources([moved])), _row(succeeded)
        ],
    )
    assert service._last_resolutions(
        entity_id=_ENTITY, bot_id=_BOT
    ) == {"charts": "b" * 40}




def test_a_failed_handoff_has_no_session_to_leak(world, monkeypatch):
    """The terminal path that never runs ``_run``, after the work moved.

    W7 wrote this against ``Thread.start`` raising: the session was built in
    the request, so a launch that never ran ``_run`` had to close it by hand
    or leak an ``mkdtemp`` tree. W13 builds the session in the handler
    instead — the checkouts belong to the process that applies them — so the
    leak this guarded against cannot form: a handoff that fails has no
    session yet. That is the invariant now, and it is worth pinning rather
    than deleting, because moving the build back into ``start_apply`` would
    silently restore the leak the launch-failure path no longer closes.
    """
    service, _applies, _locks, _scripts, _manifests = world
    queue = service._task_queue_provider()
    closed = _counting_session_closes(monkeypatch)
    before = FakeGitClient.constructed

    class _QueueUnavailableError(RuntimeError):
        pass

    def _explode(*args, **kwargs):
        raise _QueueUnavailableError("could not reach the queue")

    monkeypatch.setattr(queue, "enqueue", _explode)

    with pytest.raises(_QueueUnavailableError):
        _start(service)

    assert FakeGitClient.constructed == before, (
        "start_apply built a source session before the handoff; its checkout "
        "trees would outlive the request that made them"
    )
    assert closed == [], "nothing was built, so nothing was there to close"


def test_a_dry_run_closes_its_session(world, monkeypatch):
    """The dry run builds a session too (it may fetch through it), and has
    no background thread — its own ``finally`` is the only place the close
    can live."""
    import asyncio

    service, _applies, _locks, _scripts, _manifests = world
    closed = _counting_session_closes(monkeypatch)
    before = FakeGitClient.constructed

    report = asyncio.run(
        service.dry_run(
            entity_id=_ENTITY,
            bot_id=_BOT,
            bot=_BOT_RECORD,
            owner_id=_ENTITY,
            actor_id=_ENTITY,
        )
    )
    assert report.status is ApplyStatus.SUCCEEDED
    assert FakeGitClient.constructed - before == 1
    assert len(closed) == 1, "a dry run must close its session before returning"
# ── Carrying one phase's report into the next (W13) ────────────────────────
#
# A creation runs two applies — the pre-container phase writes `script`, the
# post-container phase writes everything else — separated by the whole of
# container provisioning. Each has its own record, so the report a caller reads
# at the end has to account for both or the manifest looks half-vanished.


def test_the_second_phase_report_carries_the_first_phases_categories(world):
    service, applies, _locks, _scripts, _manifests = world

    first = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        trigger="create:pre_container",
        phases=frozenset({ApplyPhase.PRE_CONTAINER}),
    )
    second = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        trigger="create:on_container",
        phases=frozenset({ApplyPhase.ON_CONTAINER}),
        carry_from_apply_id=first.apply_id,
    )

    merged = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id=second.apply_id
    )
    names = [c.construct.value for c in merged.categories]
    assert "script" in names, (
        "the post-container report dropped the pre-container phase's category; "
        "a caller reading it would think the script never landed"
    )
    # APPLY_ORDER's own order: script is position 0.
    assert names[0] == "script"

    # The carried-from record is not rewritten by being carried.
    earlier = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id=first.apply_id
    )
    assert [c.construct.value for c in earlier.categories] == ["script"]


def test_a_missing_carry_id_is_ignored_rather_than_failing_the_apply(world):
    service, _applies, _locks, _scripts, _manifests = world

    accepted = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        trigger="create:on_container",
        carry_from_apply_id="does-not-exist",
        phases=ALL_PHASES,
    )

    report = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id=accepted.apply_id
    )
    assert report is not None
    assert report.status is ApplyStatus.SUCCEEDED, (
        "losing a reporting nicety must never fail an apply that worked"
    )


def test_a_failed_first_phase_survives_the_merge_and_re_derives_the_summary(world):
    """The summary is re-derived over the union, not copied from this phase.

    This is the case the merge exists for. On its own the post-container phase
    here delivers nothing and reports ``SUCCEEDED``; the pre-container phase
    failed. A caller reading only the second report would be told the manifest
    applied cleanly, which is exactly the lie the carry prevents.

    The union's status is ``FAILED`` rather than ``PARTIAL`` because this
    fixture's document declares only ``script``: nothing was delivered at all.
    ``PARTIAL`` needs a document where something *did* land, which the
    orchestrator's own suite covers — what matters here is that the summary
    moved off what this phase alone would have said.
    """
    service, _applies, _locks, scripts, _manifests = world

    def _refuse(**_kwargs):
        raise RuntimeError("startup script write failed")

    scripts.put = _refuse
    first = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        trigger="create:pre_container",
        phases=frozenset({ApplyPhase.PRE_CONTAINER}),
    )
    assert (
        service.get_apply(
            entity_id=_ENTITY, bot_id=_BOT, apply_id=first.apply_id
        ).status
        is ApplyStatus.FAILED
    )

    second = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        trigger="create:on_container",
        phases=frozenset({ApplyPhase.ON_CONTAINER}),
        carry_from_apply_id=first.apply_id,
    )
    merged = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id=second.apply_id
    )
    assert "script" in [c.construct.value for c in merged.categories], (
        "the failed phase's category did not survive the merge"
    )
    assert merged.status is not ApplyStatus.SUCCEEDED, (
        "the summary was copied from this phase instead of re-derived over the "
        "union; a caller would be told the script landed when it did not"
    )
    assert merged.status is ApplyStatus.FAILED


# ── What can actually be applied in this build (W13) ───────────────────────


def test_materialised_constructs_reports_what_is_registered_today(world):
    """W4 registered two; W5 brought the count to four, W6 to five, W9 to six.

    Pinned as an equality rather than a subset so a materialiser that lands
    without its documentation, its ordering entry or its capability gate is
    caught here — the set is a published contract, not an accident of imports.
    Widening it is the whole edit a new materialiser needs, which is the
    property the next test pins.
    """
    service, *_ = world
    assert {c.value for c in service.materialised_constructs()} == {
        "script",
        "mcp",
        "identity",
        "skills",
        "resources",
        "cli_tools",
    }


def test_registering_a_materialiser_widens_it_with_no_edit_to_any_caller(world):
    """The property W5 and W6 depend on.

    A hand-written set would need an edit here when they land, and forgetting it
    is invisible until a creation accepts a category nothing can apply — after a
    Passport application, a user's click, and a live bot. Deriving it from the
    registry makes landing a materialiser the whole change.
    """
    service, *_ = world
    from agentclaw.community.core.bot_config_manifest.capabilities import (
        ManifestCategory,
    )

    real = service._build_materialisers

    class _StubResourcesMaterialiser:
        construct = ManifestCategory.RESOURCES

    def _widened():
        built = dict(real())
        built[ManifestCategory.RESOURCES] = _StubResourcesMaterialiser()
        return built

    service._build_materialisers = _widened
    try:
        assert ManifestCategory.RESOURCES in service.materialised_constructs()
    finally:
        service._build_materialisers = real


def test_an_apply_that_cannot_be_rebuilt_terminates_instead_of_looping(world):
    """A rebuild failure is terminal, not a retry.

    The document is re-read at execution rather than carried in the payload, so
    it can fail to validate then — the bot's engine changed since the enqueue,
    say. Letting that escape hands the worker an exception it treats as a retry,
    and the apply would loop until its deadline with the lock still held and the
    record still RUNNING. Both halves are asserted here because leaving either
    out is the bug: no report means a poller waits, no release means the bot is
    locked against every future apply.
    """
    service, _applies, locks, _scripts, _manifests = world

    accepted = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        phases=ALL_PHASES,
    )
    # A second apply, whose rebuild will fail.
    def _refuse(**_kwargs):
        raise RuntimeError("the stored document no longer validates")

    service._parsed_or_empty = _refuse
    payload = {
        "apply_id": "apply-that-cannot-start",
        "entity_id": _ENTITY,
        "bot_id": _BOT,
        "owner_id": _ENTITY,
        "actor_id": _ENTITY,
        "env": get_current_env(),
        "tenant": "",
        "trigger": "explicit",
        "lock_token": "no-such-token",
        "started_at": None,
        "phases": None,
        "carry_from_apply_id": None,
        "engine_type": None,
        "bot_type": None,
    }
    service._applies.start(
        env=get_current_env(),
        entity_id=_ENTITY,
        bot_id=_BOT,
        apply_id="apply-that-cannot-start",
        trigger="explicit",
        actor=_ENTITY,
        report="{}",
    )

    # Must not raise: raising is what the worker turns into a doomed retry.
    service.run_apply_task(payload)

    stranded = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id="apply-that-cannot-start"
    )
    assert stranded.status is ApplyStatus.FAILED, (
        "an apply that cannot be rebuilt must terminate, not poll forever"
    )
    assert accepted.apply_id  # the first apply is untouched
