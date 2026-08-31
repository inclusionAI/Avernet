"""The service's lifecycle: serialization, the two writes, and the stale case.

These are the criteria the orchestrator tests cannot reach, because they are
about what happens *around* the engine — the lock, the record's two writes, and
what a poller sees. Run against in-memory SQLite through the real repository
bodies, so the UNIQUE-constraint-as-lock is exercised for real rather than
mocked.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
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

from ._fakes import FakeActivationService, FakeMcpAuth, FakeStartupScriptService

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
    )
    return service, applies, locks, scripts, BotConfigManifestRepository(db)


def _start(service):
    return service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
    )


def _drain(service):
    """Let the background thread finish, then read the terminal report."""
    import time

    for _ in range(200):
        report = service.last_apply(entity_id=_ENTITY, bot_id=_BOT)
        if report is not None and report.status is not ApplyStatus.RUNNING:
            return report
        time.sleep(0.01)
    raise AssertionError("the apply never reached a terminal status")


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
