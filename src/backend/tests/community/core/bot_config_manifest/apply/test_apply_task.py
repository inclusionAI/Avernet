"""Applying as a durable task: what re-running one costs, and what a lost one leaves.

W4 ran an apply on a daemon thread. W13 makes that loss load-bearing — creation
waits on an apply completing — so the work moved onto ``core/task_queue``, and
with it comes a property the thread never had:

**The queue invokes at least once.** A crashed worker's task is re-claimed the
moment its lease expires, whether or not any handler asked for a retry. There is
no "retry off" to configure and never was; ``Retry`` decides *backoff*, not
whether a lease can expire.

So re-running has to be safe by construction, and it is — for two reasons, both
tested below and neither of them a queue setting:

* **Apply converges.** Re-applying an unchanged document performs no writes,
  because every materialiser compares before it writes. This is the property a
  new materialiser could break, and nothing about the queue would save it.
* **The lock serialises attempts.** It is acquired by whoever enqueues and
  released by whoever runs, so two live attempts on one bot cannot exist —
  and a task that never runs at all leaves a lock that expires on its own.

What is *not* here: the lock's own lifecycle, the two record writes, and what a
poller sees. Those are ``test_apply_service_lifecycle.py``'s, and they did not
change when the executor did — which is the point of how the split was chosen.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_config_manifest.apply.apply_task import (
    APPLY_TASK_DEADLINE_SECONDS,
    APPLY_TASK_TYPE,
    ApplyTaskHandler,
    LEGACY_PHASES_KEY,
    PHASE_KEY,
    build_apply_task_payload,
    phase_from_payload,
    phase_of_payload,
)
from agentclaw.community.core.bot_config_manifest.apply.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus

# Imported for side effect: registers the models on ``Base.metadata``.
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
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyLockRepository,
    BotConfigManifestApplyRepository,
)
from agentclaw.community.core.task_queue.types import Complete

from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    DeclaredSourceResolver,
)
from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeGitClient,
    FakeIdentityService,
    FakeManifestContent,
    FakeMcpAuth,
    FakeResourceFileService,
    FakeSkillUploadService,
    FakeStartupScriptService,
    real_validator,
)
from tests.community.core.bot_config_manifest.apply._fakes import FakeObjectStore

_ENTITY = "u_owner"
_BOT = "b_task"
_DOCUMENT = 'schema_version: 1\nscript:\n  body: "echo hello"\n'
_BOT_RECORD = {
    "bot_id": _BOT,
    "owner_id": _ENTITY,
    "entity_id": _ENTITY,
    "active_engine": "claude_code",
    "bot_type": "personal",
}


class _Db:
    def __init__(self, engine) -> None:
        self._sessions = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._sessions()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _Manifests:
    def __init__(self, *, raise_on_get: bool = False) -> None:
        self._raise_on_get = raise_on_get

    def get(self, *, entity_id, bot_id):
        if self._raise_on_get:
            raise RuntimeError("the manifest store is unreachable")
        return type("R", (), {"document": _DOCUMENT})()

    def validate(self, *, document, active_engine, bot_type):
        import yaml

        return type("V", (), {"parsed": yaml.safe_load(document)})()

    def capabilities_for_bot(self, bot):
        return self.resolve_capabilities(
            active_engine=bot.get("active_engine"), bot_type=bot.get("bot_type")
        )

    def resolve_capabilities(self, *, active_engine, bot_type):
        from agentclaw.community.core.bot_config_manifest.capabilities import (
            resolve_capabilities,
        )

        return resolve_capabilities(
            active_engine=active_engine,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        )


class _Bots:
    def get_by_id_and_entity(self, bot_id, entity_id):
        return _BOT_RECORD


class _HoldingQueue:
    """A queue that keeps the payload instead of running it.

    The point of the split: ``start_apply`` acquires the lock, writes the
    RUNNING row and hands the work over, all synchronously. Holding the payload
    here is a task that has been enqueued and not yet claimed — which is exactly
    the state a worker outage leaves behind, and the state the lock has to
    survive.
    """

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
        assert task_type == APPLY_TASK_TYPE
        assert deadline_seconds == APPLY_TASK_DEADLINE_SECONDS
        self.payloads.append(payload)
        return (None, True)


def _service(db, *, scripts, manifests=None, queue=None):
    return BotConfigManifestApplyService(
        manifest_service=manifests or _Manifests(),
        apply_repository=BotConfigManifestApplyRepository(db),
        lock_repository=BotConfigManifestApplyLockRepository(db),
        script_service_provider=lambda: scripts,
        activation_service_provider=lambda: FakeActivationService(),
        mcp_auth_service_provider=lambda: FakeMcpAuth(),
        # W5's materialisers. This suite's document declares only script, so the
        # fetch-consuming categories are never reached — but they must exist for
        # the registry to register.
        identity_service_provider=lambda: FakeIdentityService(),
        upload_service_provider=lambda: FakeSkillUploadService(),
        capability_reader_provider=lambda: FakeCapabilityReader(),
        package_validator_provider=lambda: real_validator(),
        entry_fetcher_provider=lambda: DeclaredSourceResolver(
            FakeManifestContent(), FakeCredentials(), FakeObjectStore()
        ),
        # W6's resources materialiser and W7's git transport: unreached by
        # this suite's script-only document, but the registry registers them
        # and the session is built per apply regardless.
        resource_service_provider=lambda: FakeResourceFileService(),
        cli_tool_service_factory=lambda family: None,
        git_client_provider=lambda: FakeGitClient(),
        task_queue_provider=lambda: queue,
        bot_repository=_Bots(),
    )


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    db = _Db(engine)
    queue = _HoldingQueue()
    scripts = FakeStartupScriptService()
    service = _service(db, scripts=scripts, queue=queue)
    locks = BotConfigManifestApplyLockRepository(db)
    return service, queue, scripts, locks


def _start(service):
    return service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
    )


# ── the payload ────────────────────────────────────────────────────────────


def test_the_payload_carries_identifiers_and_not_the_document():
    """64 KB is the manifest's cap *and* the payload column's, so both cannot fit.

    A non-strict MySQL truncates rather than raising, which would corrupt the
    task silently. The handler re-reads instead — and the behaviour that implies,
    reading the document as of execution rather than at enqueue, is stated where
    the payload is built.
    """
    payload = build_apply_task_payload(
        apply_id="a1",
        entity_id=_ENTITY,
        bot_id=_BOT,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        env="dev",
        tenant="t1",
        trigger="explicit",
        lock_token="tok",
        started_at="2026-09-01T00:00:00",
        phase=None,
    )
    assert "document" not in payload and "parsed" not in payload
    assert "bot" not in payload
    # The tenant *is* carried: the queue has no tenant column, and no request
    # context survives to handler time.
    assert payload["tenant"] == "t1"


def test_the_payload_states_the_phase_and_round_trips_it():
    """What an apply covers is written down, never inferred at the far end.

    ``null`` is one of the stated values, not an absence: it says this apply is
    not a creation half, which is the whole document.
    """
    def payload_for(phase):
        return build_apply_task_payload(
            apply_id="a1", entity_id=_ENTITY, bot_id=_BOT, owner_id=_ENTITY,
            actor_id=_ENTITY, env="dev", tenant="t1", trigger="explicit",
            lock_token="tok", started_at="2026-09-01T00:00:00", phase=phase,
        )

    whole = payload_for(None)
    assert "phase" in whole, (
        "the payload left out what the apply covers; the far end would have to "
        "reconstruct it from a default"
    )
    assert whole["phase"] is None
    assert phase_from_payload(whole["phase"]) is None

    for phase in ApplyPhase:
        written = payload_for(phase)["phase"]
        assert written == phase.value
        assert phase_from_payload(written) is phase


def test_an_unknown_phase_name_in_a_payload_raises():
    """Dropping it would silently narrow or widen what an apply covers."""
    with pytest.raises(ValueError):
        phase_from_payload("mid_container")


# ── reading a payload the previous version enqueued ────────────────────────
#
# The queue is durable and a rollout is not atomic, so for one deploy a worker
# running the scalar contract can claim a task written under the list one.
# These pin the translation; delete them with ``LEGACY_PHASES_KEY`` once no
# queue can hold such a payload.


@pytest.mark.parametrize(
    "legacy, expected",
    [
        (["pre_container"], ApplyPhase.PRE_CONTAINER),
        (["on_container"], ApplyPhase.ON_CONTAINER),
        # What ALL_PHASES serialised to, in the builder's sorted order.
        (["on_container", "pre_container"], None),
        # Order was never meaningful; a payload is a payload either way round.
        (["pre_container", "on_container"], None),
    ],
)
def test_a_legacy_payload_keeps_the_half_it_named(legacy, expected):
    """The widening this prevents is the one that matters.

    A creation's pre-container task read as a whole apply would walk the
    container-bound constructs before any container exists — the ordering
    failure ``ApplyPhase`` exists to prevent — and it would do it silently,
    because nothing downstream re-checks what the enqueuer meant.
    """
    assert phase_of_payload({"phases": legacy}) is expected


def test_the_scalar_key_wins_and_its_null_is_not_an_absence():
    """Presence of ``"phase"``, not its value, says which contract is spoken.

    The builder always writes the key, so a payload carrying ``None`` there is
    stating "not a creation half" — it must never fall through to the legacy
    branch, even in the transitional case where both keys somehow appear.
    """
    assert phase_of_payload({"phase": None}) is None
    assert phase_of_payload({"phase": "pre_container"}) is ApplyPhase.PRE_CONTAINER
    assert phase_of_payload({"phase": None, "phases": ["pre_container"]}) is None


def test_a_payload_naming_no_phase_at_all_is_a_whole_apply():
    """Neither key: nothing to widen, so the whole apply is the honest read."""
    assert phase_of_payload({}) is None


@pytest.mark.parametrize("legacy", [[], ["mid_container"]])
def test_an_unreadable_legacy_payload_raises_rather_than_guessing(legacy):
    """An empty list named an apply covering nothing — no caller meant that,
    and the scalar cannot express it. An unknown name is not silently dropped
    either: both would change what the apply covers without saying so."""
    with pytest.raises(ValueError):
        phase_of_payload({"phases": legacy})


def test_the_service_reads_a_legacy_payload_through_the_translation(world):
    """End to end through ``run_apply_task``, the path a worker actually takes.

    This suite's document declares only ``script``, which is ``PRE_CONTAINER``
    on ARCA — so a legacy payload naming the *post*-container half must write
    nothing at all. Widening it to a whole apply is exactly what writes the
    script here, which makes ``scripts.writes`` a direct read on the bug: 0 if
    the half survived the decode, 1 if it was widened.
    """
    service, queue, scripts, _locks = world
    _start(service)

    legacy = dict(queue.payloads[0])
    legacy["trigger"] = "create:on_container"
    del legacy[PHASE_KEY]
    legacy[LEGACY_PHASES_KEY] = ["on_container"]

    service.run_apply_task(legacy)

    assert scripts.writes == 0, (
        "a legacy post-container payload was widened into a whole apply; on a "
        "creation's pre-container half the same widening would run the "
        "container-bound constructs before any container exists"
    )


# ── re-running ─────────────────────────────────────────────────────────────


def test_running_the_same_task_twice_writes_nothing_the_second_time(world):
    """Convergence, which is what makes at-least-once delivery safe here.

    Not "retry is off" — the queue re-claims an expired lease whether or not any
    handler asked it to, so there is nothing to switch off. The second run reads
    the same document, finds the same state, and writes nothing. A materialiser
    added later that is not convergent breaks this, and no queue configuration
    would save it.
    """
    service, queue, scripts, _locks = world

    _start(service)
    payload = queue.payloads[0]

    service.run_apply_task(payload)
    after_first = scripts.writes
    assert after_first == 1, "the first run should have written the script once"

    service.run_apply_task(payload)
    assert scripts.writes == after_first, (
        "the second run wrote again: re-delivery would keep rewriting the bot's "
        "configuration every time a lease expired"
    )


def test_the_report_still_reads_terminal_after_a_second_run(world):
    """A re-run must not leave the apply looking unfinished.

    It re-acquires the lock and writes the terminal row again; what a poller must
    never see is the record going back to RUNNING and staying there.
    """
    service, queue, _scripts, locks = world

    _start(service)
    payload = queue.payloads[0]
    service.run_apply_task(payload)
    service.run_apply_task(payload)

    report = service.last_apply(entity_id=_ENTITY, bot_id=_BOT)
    assert report is not None
    assert report.status is ApplyStatus.SUCCEEDED
    assert locks.get(env="dev", entity_id=_ENTITY, bot_id=_BOT) is None


# ── the lock, across the handoff ───────────────────────────────────────────


def test_a_task_that_never_runs_leaves_a_lock_for_the_ttl_to_reap(world):
    """The lock now spans a process boundary, so this is the failure mode.

    Enqueued and never claimed — a worker outage — is the same outcome a dead
    thread had before: the lock sits there until its TTL expires. The TTL is what
    makes that recoverable without an operator, and it is deliberately far longer
    than an apply takes, so it never steals a lock from a slow one.
    """
    service, queue, _scripts, locks = world

    _start(service)
    assert queue.payloads, "the apply was never handed to the queue"

    held = locks.get(env="dev", entity_id=_ENTITY, bot_id=_BOT)
    assert held is not None, (
        "the lock is released by the handler, so an unclaimed task must still "
        "be holding it — otherwise a second apply could start alongside it"
    )
    assert APPLY_LOCK_TTL_SECONDS >= 15 * 60


def test_a_handler_with_no_payload_completes_rather_than_looping():
    """A payload-less apply task is a wiring bug, not work to retry forever."""
    handler = ApplyTaskHandler(lambda: None)
    assert isinstance(handler.handle(None), Complete)
    assert isinstance(handler.handle({}), Complete)
    assert handler.task_type == APPLY_TASK_TYPE


def test_an_apply_that_cannot_be_rebuilt_releases_its_lock(world):
    """A raise on the way *in* must not strand the bot.

    The rebuild happens before the orchestrator, so nothing has been applied and
    nothing will be — but the lock is already held, and a task that keeps failing
    to rebuild would hold it every time it was re-claimed. Terminating the apply
    releases it, and the report says the apply failed rather than leaving a
    RUNNING row nobody will ever finish.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    db = _Db(engine)
    queue = _HoldingQueue()
    scripts = FakeStartupScriptService()

    # Readable at enqueue, unreachable at execution: the window the payload's
    # re-read deliberately leaves open.
    manifests = _Manifests()
    service = _service(db, scripts=scripts, manifests=manifests, queue=queue)
    _start(service)
    manifests._raise_on_get = True

    service.run_apply_task(queue.payloads[0])

    locks = BotConfigManifestApplyLockRepository(db)
    assert locks.get(env="dev", entity_id=_ENTITY, bot_id=_BOT) is None
    report = service.last_apply(entity_id=_ENTITY, bot_id=_BOT)
    assert report is not None and report.status is ApplyStatus.FAILED
    assert scripts.writes == 0, "nothing should have been applied"
