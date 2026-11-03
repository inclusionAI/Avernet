"""Bot-create human-authorization surfacing on the run composition.

Pins the composition join over real repositories: the `authorization`
descriptor on run detail (and on the SSE authorization snapshot) keys off
the authorization source's live pending answer, not the agent row's
cached `bot_state` write — parallel cohort agents go PENDING at the
facade long before the pool parks their rows, and the run surface must
surface every gate a human can still act on, identified by
`application_id`. Terminal agents and non-bot agents carry none, and
per-bot independence holds: two pending agents surface two distinct
descriptors. The join is read-side only: no swarm row stores a URL or
deadline.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmTenantLimits, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.domain.models import TaskEnvelope
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.agent_result import (
    AgentResultRepository,
)
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import (
    RunDispatchRepository,
)
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmRunNotFoundError, BotAuthorizationDescriptor,
)

TENANT = "tenant-a"
OWNER = "staff-1"


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

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

    session = orm_session


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        om.AcAgentSwarmRunModel.__table__,
        om.AcAgentSwarmAgentModel.__table__,
        om.AcAgentSwarmMessageModel.__table__,
        om.AcAgentSwarmAgentResultModel.__table__,
        om.AcAgentSwarmRoundModel.__table__,
        om.AcAgentSwarmGuardEventModel.__table__,
        om.AcAgentSwarmRunDispatchModel.__table__,
        om.AcAgentSwarmAgentAttemptModel.__table__,
    ])
    yield _TestDB(engine)
    engine.dispose()


class FakeAuthorizationSource:
    """BotAuthorizationSource double: pending oracle over payload facts.

    Serves a descriptor only for a bot whose durable creation payload
    exists AND whose live application reads PENDING — the same two-read
    contract the concrete source follows.
    """

    def __init__(self, payloads: dict[str, dict | None],
                 fail_for: set[str] | None = None,
                 statuses: dict[str, str] | None = None) -> None:
        self._payloads = payloads
        self._fail_for = fail_for or set()
        self._statuses = statuses or {}
        self.calls: list[tuple[str, str]] = []

    def authorization(self, *, owner_id: str,
                     bot_id: str) -> BotAuthorizationDescriptor | None:
        self.calls.append((owner_id, bot_id))
        if bot_id in self._fail_for:
            raise RuntimeError("payload backend unavailable")
        payload = self._payloads.get(bot_id)
        if payload is None:
            return None
        if self._statuses.get(bot_id, "PENDING") != "PENDING":
            return None
        return BotAuthorizationDescriptor(
            state=dm.BotState.AWAITING_AUTHORIZATION.value,
            iframe_url=str(payload.get("iframe_url") or ""),
            redirect_url=str(payload.get("redirect_url") or ""),
            deadline=str(payload.get("deadline") or ""),
            application_id=str(payload.get("application_id") or ""),
        )


def _mk_service(db, source=None) -> AgentSwarmService:
    return AgentSwarmService(
        run_repo=RunRepository(db),
        dispatch_repo=RunDispatchRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        result_repo=AgentResultRepository(db),
        attempt_repo=AgentAttemptRepository(db),
        config=AgentSwarmConfig(
            llm=LlmConfig(base_url="http://llm-stub"),
            tenant_limits=AgentSwarmTenantLimits(max_concurrent_runs=8),
        ),
        authorization_source=source,
    )


def _mk_agent(run_id: str, name: str, *, bot_id: str | None,
              bot_state: dm.BotState | None,
              kind: dm.AgentKind = dm.AgentKind.BOT,
              status: dm.AgentStatus = dm.AgentStatus.RUNNING) -> dm.Agent:
    return dm.Agent(
        agent_id=dm.AgentId(uuid.uuid4().hex),
        run_id=dm.RunId(run_id),
        tenant=TENANT,
        parent_agent_id=None,
        depth=0,
        name=name,
        role="research",
        kind=kind,
        task_spec=TaskEnvelope(
            name=name, objective=f"objective of {name}",
            output_schema={"type": "object"},
            boundaries="stay in scope", parent_goal="run goal",
        ),
        spawn_round=0,
        status=status,
        attempts=1,
        idempotency_key=dm.IdempotencyKey(f"{run_id}:{name}"),
        output={},
        bot_id=dm.BotId(bot_id) if bot_id else None,
        bot_state=bot_state,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
    )


def _mk_llm_agent(run_id: str, name: str) -> dm.Agent:
    return _mk_agent(run_id, name, bot_id=None, bot_state=None,
                     kind=dm.AgentKind.LLM)


def _mk_run(owner_id: str = OWNER) -> dm.Run:
    return dm.Run(
        run_id=dm.RunId(uuid.uuid4().hex),
        tenant=dm.TenantId(TENANT),
        goal="test goal",
        agent_catalog=(dm.AgentSpec(name="r1", role="research"),),
        provider=dm.ProviderKind("stub"),
        request_metadata=dm.RequestMetadata(),
        status=dm.RunStatus.RUNNING,
        started_at=dm.EpochMillis(int(time.time() * 1000)),
        owner_id=owner_id,
    )


class TestRunDetailAuthorization:

    def test_parked_agent_carries_descriptor(self, db):
        run = _mk_run()
        parked = _mk_agent(
            run.run_id, "scout", bot_id="agent_swarm_aaaa",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        source = FakeAuthorizationSource({
            "agent_swarm_aaaa": {
                "iframe_url": "https://tcauthmng.example/consent?app=p1",
                "redirect_url": "https://tcauthmng.example/done-p1",
                "deadline": "2026-11-20T10:10:00",
                "application_id": "sim-passport-0001",
            },
        })
        RunRepository(db).create(run)
        AgentRepository(db).create(parked)
        service = _mk_service(db, source)

        detail = service.get_run_detail(run.run_id, TENANT)

        (item,) = detail.agents
        assert item.bot_state == "AWAITING_AUTHORIZATION"
        assert item.authorization is not None
        assert item.authorization.state == dm.BotState.AWAITING_AUTHORIZATION.value
        assert item.authorization.iframe_url == \
            "https://tcauthmng.example/consent?app=p1"
        assert item.authorization.redirect_url == \
            "https://tcauthmng.example/done-p1"
        assert item.authorization.deadline == "2026-11-20T10:10:00"
        assert item.authorization.application_id == "sim-passport-0001"
        assert source.calls == [(OWNER, "agent_swarm_aaaa")]

    def test_pending_before_the_park_write_still_carries_descriptor(self, db):
        run = _mk_run()
        # The bug's shape: parallel cohort agents applied and PENDING at
        # the facade while the pool has not yet reached their setup, so
        # their rows carry no park write at all.
        queued = _mk_agent(
            run.run_id, "later", bot_id="agent_swarm_q2", bot_state=None,
        )
        warming = _mk_agent(
            run.run_id, "warming", bot_id="agent_swarm_q3",
            bot_state=dm.BotState.PROVISIONING,
        )
        source = FakeAuthorizationSource({
            "agent_swarm_q2": {
                "iframe_url": "https://tcauthmng.example/consent?app=q2",
                "redirect_url": "https://tcauthmng.example/done-q2",
                "deadline": "2026-11-20T10:10:00",
                "application_id": "sim-passport-q2",
            },
            "agent_swarm_q3": {
                "iframe_url": "https://tcauthmng.example/consent?app=q3",
                "redirect_url": "https://tcauthmng.example/done-q3",
                "deadline": "2026-11-20T10:12:00",
                "application_id": "sim-passport-q3",
            },
        })
        RunRepository(db).create(run)
        repo = AgentRepository(db)
        repo.create(queued)
        repo.create(warming)
        service = _mk_service(db, source)

        detail = service.get_run_detail(run.run_id, TENANT)

        by_name = {a.name: a for a in detail.agents}
        for name, bot_state, app_id in (
            ("later", None, "sim-passport-q2"),
            ("warming", "PROVISIONING", "sim-passport-q3"),
        ):
            item = by_name[name]
            assert item.bot_state == bot_state
            assert item.authorization is not None
            assert item.authorization.state == \
                dm.BotState.AWAITING_AUTHORIZATION.value
            assert item.authorization.application_id == app_id
            assert item.authorization.iframe_url.startswith(
                "https://tcauthmng.example/consent?app=")
        assert sorted(source.calls) == [
            (OWNER, "agent_swarm_q2"), (OWNER, "agent_swarm_q3"),
        ]

    def test_terminal_agent_keeps_no_gate(self, db):
        run = _mk_run()
        killed = _mk_agent(
            run.run_id, "dead", bot_id="agent_swarm_k",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
            status=dm.AgentStatus.KILLED,
        )
        source = FakeAuthorizationSource({
            "agent_swarm_k": {
                "iframe_url": "https://tcauthmng.example/consent?app=k",
                "application_id": "sim-passport-k",
            },
        })
        RunRepository(db).create(run)
        AgentRepository(db).create(killed)
        service = _mk_service(db, source)

        detail = service.get_run_detail(run.run_id, TENANT)

        (item,) = detail.agents
        # A terminal agent's detail keeps its pre-change shape: no gate
        # join, no source read.
        assert item.status == "KILLED"
        assert item.authorization is None
        assert source.calls == []

    def test_two_parked_agents_surface_independent_descriptors(self, db):
        run = _mk_run()
        first = _mk_agent(
            run.run_id, "scout", bot_id="agent_swarm_aa",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        second = _mk_agent(
            run.run_id, "writer", bot_id="agent_swarm_bb",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        source = FakeAuthorizationSource({
            "agent_swarm_aa": {
                "iframe_url": "https://tcauthmng.example/consent?app=aa",
                "redirect_url": "https://tcauthmng.example/done-aa",
                "deadline": "2026-11-20T10:10:00",
                "application_id": "sim-passport-aa",
            },
            "agent_swarm_bb": {
                "iframe_url": "https://tcauthmng.example/consent?app=bb",
                "redirect_url": "https://tcauthmng.example/done-bb",
                "deadline": "2026-11-20T11:11:00",
                "application_id": "sim-passport-bb",
            },
        })
        RunRepository(db).create(run)
        repo = AgentRepository(db)
        repo.create(first)
        repo.create(second)
        service = _mk_service(db, source)

        detail = service.get_run_detail(run.run_id, TENANT)

        by_name = {a.name: a for a in detail.agents}
        assert by_name["scout"].authorization.iframe_url.endswith("app=aa")
        assert by_name["scout"].authorization.redirect_url.endswith("done-aa")
        assert by_name["scout"].authorization.deadline == "2026-11-20T10:10:00"
        assert by_name["scout"].authorization.application_id == "sim-passport-aa"
        assert by_name["writer"].authorization.iframe_url.endswith("app=bb")
        assert by_name["writer"].authorization.redirect_url.endswith("done-bb")
        assert by_name["writer"].authorization.deadline == "2026-11-20T11:11:00"
        assert by_name["writer"].authorization.application_id == "sim-passport-bb"

    def test_mixed_run_descriptor_only_for_pending(self, db):
        run = _mk_run()
        parked = _mk_agent(
            run.run_id, "parked", bot_id="agent_swarm_p",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        # D7 fast path: passport issued inline — plain provisioning, no
        # gate even though its job payload carries consent URLs.
        auto_issued = _mk_agent(
            run.run_id, "fast", bot_id="agent_swarm_f",
            bot_state=dm.BotState.PROVISIONING,
        )
        ready = _mk_agent(
            run.run_id, "ready", bot_id="agent_swarm_r",
            bot_state=dm.BotState.READY,
        )
        rejected = _mk_agent(
            run.run_id, "rejected", bot_id="agent_swarm_x",
            bot_state=dm.BotState.AUTHORIZATION_REJECTED,
        )
        llm = _mk_llm_agent(run.run_id, "llm")
        RunRepository(db).create(run)
        repo = AgentRepository(db)
        for a in (parked, auto_issued, ready, rejected, llm):
            repo.create(a)
        source = FakeAuthorizationSource(
            {
                "agent_swarm_f": {
                    "iframe_url": "https://tcauthmng.example/consent?app=f",
                    "redirect_url": "https://tcauthmng.example/done-f",
                    "deadline": "2026-11-20T10:10:00",
                    "application_id": "sim-passport-f",
                },
            },
            statuses={"agent_swarm_f": "ISSUED"},
        )
        service = _mk_service(db, source)

        detail = service.get_run_detail(run.run_id, TENANT)

        by_name = {a.name: a for a in detail.agents}
        assert by_name["parked"].authorization is not None
        assert by_name["parked"].authorization.iframe_url == ""
        assert by_name["fast"].authorization is None
        assert by_name["ready"].authorization is None
        assert by_name["rejected"].authorization is None
        assert by_name["llm"].authorization is None
        # Every live bot-kind agent is asked — only the llm-kind row
        # never reaches the source.
        assert sorted(source.calls) == [
            (OWNER, "agent_swarm_f"), (OWNER, "agent_swarm_p"),
            (OWNER, "agent_swarm_r"), (OWNER, "agent_swarm_x"),
        ]

    def test_nonparked_detail_shape_is_unchanged(self, db):
        run = _mk_run()
        ready = _mk_agent(run.run_id, "fast", bot_id="agent_swarm_f",
                          bot_state=dm.BotState.READY)
        RunRepository(db).create(run)
        AgentRepository(db).create(ready)
        service = _mk_service(db, FakeAuthorizationSource({}))

        detail = service.get_run_detail(run.run_id, TENANT)

        (item,) = detail.agents
        # Regression pin: every pre-existing field keeps its exact value;
        # the only change is the additive authorization=None.
        assert item.agent_id == ready.agent_id
        assert item.name == "fast"
        assert item.role == "research"
        assert item.kind == "bot"
        assert item.status == "RUNNING"
        assert item.parent_agent_id is None
        assert item.depth == 0
        assert item.spawn_round == 0
        assert item.idempotency_key == f"{run.run_id}:fast"
        assert item.attempts == 1
        assert item.bot_id == "agent_swarm_f"
        assert item.bot_state == "READY"
        assert item.error is None
        assert item.output == {}
        assert item.authorization is None

    def test_unresolvable_park_surfaces_gate_with_none_fields(self, db):
        run = _mk_run()
        parked = _mk_agent(
            run.run_id, "lonely", bot_id="agent_swarm_z",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        RunRepository(db).create(run)
        AgentRepository(db).create(parked)

        degraded_sources = [
            # No durable payload exists for the parked bot.
            FakeAuthorizationSource({}),
            # The payload read fails: the poll degrades, never fails.
            FakeAuthorizationSource({}, fail_for={"agent_swarm_z"}),
            # No source wired at all (embedded/test composition).
            None,
        ]
        for source in degraded_sources:
            service = _mk_service(db, source)
            detail = service.get_run_detail(run.run_id, TENANT)
            (item,) = detail.agents
            assert item.bot_state == "AWAITING_AUTHORIZATION"
            assert item.authorization is not None
            assert item.authorization.state == dm.BotState.AWAITING_AUTHORIZATION.value
            assert item.authorization.iframe_url == ""
            assert item.authorization.redirect_url == ""
            assert item.authorization.deadline == ""

    def test_park_without_bot_id_never_touches_the_seam(self, db):
        run = _mk_run()
        parked = _mk_agent(run.run_id, "botless", bot_id=None,
                           bot_state=dm.BotState.AWAITING_AUTHORIZATION)
        RunRepository(db).create(run)
        AgentRepository(db).create(parked)
        source = FakeAuthorizationSource({})
        service = _mk_service(db, source)

        detail = service.get_run_detail(run.run_id, TENANT)

        (item,) = detail.agents
        assert item.authorization is not None
        assert item.authorization.iframe_url == ""
        assert source.calls == []


class TestRunEventsAuthorizationSnapshot:

    def test_snapshot_carries_bot_states_and_parked_descriptors(self, db):
        run = _mk_run()
        parked = _mk_agent(
            run.run_id, "scout", bot_id="agent_swarm_aaaa",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        auto_issued = _mk_agent(
            run.run_id, "fast", bot_id="agent_swarm_bbbb",
            bot_state=dm.BotState.PROVISIONING,
        )
        llm = _mk_llm_agent(run.run_id, "llm")
        RunRepository(db).create(run)
        repo = AgentRepository(db)
        for a in (parked, auto_issued, llm):
            repo.create(a)
        service = _mk_service(db, FakeAuthorizationSource({
            "agent_swarm_aaaa": {
                "iframe_url": "https://tcauthmng.example/consent?app=p1",
                "redirect_url": "https://tcauthmng.example/done-p1",
                "deadline": "2026-11-20T10:10:00",
                "application_id": "sim-passport-p1",
            },
        }))

        events = service.run_events(run.run_id, TENANT)

        by_name = {item.name: item for item in events.agent_authorizations}
        assert set(by_name) == {"scout", "fast"}
        assert by_name["scout"].agent_id == parked.agent_id
        assert by_name["scout"].status == "RUNNING"
        assert by_name["scout"].kind == "bot"
        assert by_name["scout"].spawn_round == 0
        assert by_name["scout"].bot_id == "agent_swarm_aaaa"
        assert by_name["scout"].bot_state == "AWAITING_AUTHORIZATION"
        assert by_name["scout"].authorization.iframe_url == \
            "https://tcauthmng.example/consent?app=p1"
        assert by_name["scout"].authorization.application_id == \
            "sim-passport-p1"
        assert by_name["fast"].bot_state == "PROVISIONING"
        assert by_name["fast"].authorization is None

    def test_snapshot_uses_run_owner_for_the_seam(self, db):
        run = _mk_run()
        parked = _mk_agent(
            run.run_id, "scout", bot_id="agent_swarm_aaaa",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        RunRepository(db).create(run)
        AgentRepository(db).create(parked)
        source = FakeAuthorizationSource({})
        service = _mk_service(db, source)

        service.run_events(run.run_id, TENANT)

        assert source.calls == [(OWNER, "agent_swarm_aaaa")]

    def test_agent_row_cursor_semantics_unchanged(self, db):
        run = _mk_run()
        parked = _mk_agent(
            run.run_id, "scout", bot_id="agent_swarm_aaaa",
            bot_state=dm.BotState.AWAITING_AUTHORIZATION,
        )
        RunRepository(db).create(run)
        AgentRepository(db).create(parked)
        service = _mk_service(db, FakeAuthorizationSource({}))

        first = service.run_events(run.run_id, TENANT)
        (row_cursor,) = [a.row_cursor for a in first.agents]

        # After the cursor: the durable agent row emits exactly once per
        # stream, exactly as before the change.
        second = service.run_events(run.run_id, TENANT, row_cursor, 0, 0)
        assert second.agents == ()
        # The authorization snapshot is per-poll state, not a row cursor:
        # a mid-stream re-read still observes every bot's current state.
        assert second.agent_authorizations == first.agent_authorizations

    def test_fast_path_is_asked_and_surfaces_no_gate(self, db):
        run = _mk_run()
        fast = _mk_agent(
            run.run_id, "fast", bot_id="agent_swarm_bbbb",
            bot_state=dm.BotState.PROVISIONING,
        )
        RunRepository(db).create(run)
        AgentRepository(db).create(fast)
        source = FakeAuthorizationSource({})
        service = _mk_service(db, source)

        service.run_events(run.run_id, TENANT)
        service.get_run_detail(run.run_id, TENANT)

        # The live bot is asked on every bot-state read; a not-pending
        # answer surfaces none — the absence comes from the oracle, not
        # from skipping the read.
        assert source.calls == [(OWNER, "agent_swarm_bbbb")] * 2
        (item,) = service.get_run_detail(run.run_id, TENANT).agents
        assert item.authorization is None


class TestUnknownRuns:

    def test_detail_and_events_stay_not_found(self, db):
        service = _mk_service(db)
        assert service.get_run_detail("nope", TENANT) is None
        with pytest.raises(AgentSwarmRunNotFoundError):
            service.run_events("nope", TENANT)