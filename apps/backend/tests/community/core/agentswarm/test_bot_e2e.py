"""Bot-kind end-to-end: a real swarm run over the real host platform.

The two existing e2e batteries (test_e2e, test_wire_e2e) drive LLM-kind
agents only — until the host adapter landed there was nothing to bind, so
this path had no end-to-end at all. This one exercises every in-process
hop with exactly two doubles:

- ``BotService.create_bot`` stands in for provisioning (the with-manifest
  endpoint suite's stand-in: device allocation posts to BaaS, the one
  collaborator a request cannot drive; it persists the row as ACTIVE, and
  everything above it is real — engine gates, quota preflight, the local
  Passport auto-issue, manifest preflight, the storage write).
- ``grt_chat`` is scripted (the engine WebSocket needs a real container);
  the adapter's thread bridge runs it exactly as it runs the real service.

Two in-memory stores on purpose: the swarm's ``ac_agent_swarm_*`` tables
under the hand-wired harness (the existing e2e shape), and the platform's
own tables under the full test-profile injector — the adapter is the only
bridge, which is the seam's whole point. A pump coroutine drives task-queue
turns (the endpoint harness's ``_Worker``: claim, dispatch the real
``BotCreateWithManifestHandler``, settle the row) while the swarm's slice
loop runs, because creation is asynchronous: the subagent polls
``bot_status``, the pump completes the job, a later poll sees READY, one
chat round trips, the result lands, the orchestrator finishes the run.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.community.framework.di_seams import bind_overrides
from tests.community.framework.world import World

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmOrchestrationConfig, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.domain.models import spawn_digest
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.orchestrator import (
    Orchestrator,
)
from agentclaw.community.core.agentswarm.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.plugins.local.agentswarm.factory import (
    DefaultSubagentFactory,
)
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.pool import SubagentRuntime
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    SubmitRunRequest,
)
from agentclaw.community.core.apply_engine.apply_task import (
    APPLY_TASK_TYPE,
)
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    CREATE_JOB_TASK_TYPE,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.api.apply_run_service import (
    ApplyRunServiceProtocol,
)
from agentclaw.community.core.task_queue.types import (
    Complete, DEFAULT_APP, Fail, Reschedule, TaskStatus,
)
from agentclaw.community.plugins.community.agentswarm.host import AvernetBotPlatform
from agentclaw.community.utils.env_utils import get_current_env

TENANT = "bot-e2e-tenant"
OWNER = "bot-e2e-owner"


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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


@pytest.fixture()
def swarm_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield _TestDB(engine)


@pytest.fixture()
def world():
    """The framework world fixture's lean core: the full test-profile DI
    graph with fresh in-memory tables, minus the HTTP app attachment and
    the publish poller — neither is ever constructed here."""
    # registers the process config provider as an import side effect, the
    # same one the framework fixture relies on — without it every config
    # read degrades to production defaults (/home/admin, say).
    from agentclaw.community.adapters.http.app import app  # noqa: F401
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.plugins.local import database as db_mod

    injector = build_injector(profile=DeployProfile.detect())
    asyncio.run(injector.get(DatabasePlugin).bootstrap())
    try:
        yield World(injector)
    finally:
        if db_mod._engine is not None:
            db_mod._engine.dispose()


def _config() -> AgentSwarmConfig:
    orch = AgentSwarmOrchestrationConfig(
        max_turns=10,
        slice_max_seconds=5.0,
        slice_interval=0.0,
        orchestrator_turn_interval=0.01,
        agent_max_attempts=1,
        batch_size=4,
    )
    return AgentSwarmConfig(
        llm=LlmConfig(base_url="stub", api_key="stub", default_model="stub"),
        orchestration=orch,
        message_retention_tail=2,
    )


def _build_swarm(db, script, platform):
    cfg = _config()
    repos = {
        "run": RunRepository(db), "agent": AgentRepository(db),
        "message": MessageRepository(db), "result": AgentResultRepository(db),
        "round": RoundRepository(db), "guard": GuardEventRepository(db),
        "dispatch": RunDispatchRepository(db),
    }
    llm = StubLLMProvider(script=script)
    orchestrator = Orchestrator(
        llm=llm,
        parser=DecisionParser(),
        guards=GuardService(
            max_width=cfg.orchestration.max_width,
            max_depth=cfg.orchestration.max_depth,
        ),
        config=cfg,
    )
    runtime = SubagentRuntime(
        factory=DefaultSubagentFactory(llm=llm, platform=platform),
        max_width=cfg.orchestration.max_width,
        max_attempts=cfg.orchestration.agent_max_attempts,
        per_task_timeout=cfg.orchestration.per_attempt_timeout,
        artifact_inline_threshold=cfg.artifact_inline_threshold,
    )
    handler = SwarmRunHandler(
        config=cfg, orchestrator=orchestrator, runtime=runtime,
        run_repo=repos["run"], agent_repo=repos["agent"],
        message_repo=repos["message"], result_repo=repos["result"],
        round_repo=repos["round"], guard_repo=repos["guard"],
        dispatch_repo=repos["dispatch"],
        termination=TerminationEvaluator(max_turns=cfg.orchestration.max_turns),
        guards=GuardService(),
    )
    worker = AgentSwarmWorker(config=cfg, handler=handler, dispatch_repo=repos["dispatch"])
    service = AgentSwarmService(
        run_repo=repos["run"], dispatch_repo=repos["dispatch"],
        agent_repo=repos["agent"], message_repo=repos["message"],
        round_repo=repos["round"], guard_repo=repos["guard"],
        result_repo=repos["result"], config=cfg,
    )
    return service, worker, repos


def _spawn_decision(*agents: tuple[str, str]) -> str:
    return json.dumps({
        "kind": "spawn_agent",
        "agents": [{"name": n, "objective": o} for n, o in agents],
    })


def _wait_decision(*refs: str) -> str:
    return json.dumps({"kind": "wait", "refs": list(refs), "mode": "all"})


def _finish_decision(answer: str) -> str:
    return json.dumps({"kind": "finish", "answer": answer})


class _ScriptedEngineWs:
    """Fake engine websocket: captures every frame the platform sends
    (sessions.reset and chat.send carry the conversation the leg served),
    answering each request with the live engine's own script — the
    reset's res, the send's ack res, then the final chat event with the
    scripted reply. The adapter's real frame protocol drives it exactly
    as it drives a live container."""

    def __init__(self, reply: str):
        self.reply = reply
        self.sent_frames: list[dict] = []
        self.closed = False
        self._answers: list[dict] = []

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent_frames.append(frame)
        method = frame.get("method")
        request_id = frame.get("id")
        if method == "sessions.reset":
            self._answers.append({
                "type": "res", "id": request_id, "ok": True,
                "payload": {"ok": True, "key": frame["params"]["key"]},
            })
        elif method == "chat.send":
            self._answers.append({
                "type": "res", "id": request_id, "ok": True,
                "payload": {"accepted": True},
            })
            self._answers.append({
                "type": "event", "event": "chat",
                "payload": {
                    "state": "final",
                    "message": {"content": [
                        {"type": "text", "text": self.reply},
                    ]},
                    "id": request_id,
                },
            })

    async def recv(self):
        assert self._answers, "unexpected recv with no request to answer"
        return json.dumps(self._answers.pop(0))

    async def close(self):
        self.closed = True


def _patch_engine_edges(monkeypatch, platform, ws):
    """Swap the two container-provided edges (BaaS ws-info resolution and
    the engine ws handshake) for the scripted websocket; every frame above
    them is the production chat leg."""
    endpoints: list[tuple[str, str]] = []

    def _endpoint(self, bot_id, owner):
        endpoints.append((bot_id, owner))
        return "ws://fake", "tok"

    async def _connect(self, ws_url, token):
        return ws

    monkeypatch.setattr(type(platform), "_engine_endpoint", _endpoint)
    monkeypatch.setattr(type(platform), "_connect_engine", _connect)
    return endpoints


def _stand_in_for_provisioning(world) -> list[dict]:
    """Persist the row ``create_bot`` would, without allocating a device.

    The one collaborator a request cannot drive: allocation posts to BaaS
    and the local ``HttpClient`` seam refuses an unstubbed call. Everything
    above it stays real, and the stand-in keeps the real one's contract —
    idempotent on a supplied ``bot_id``, so a re-claimed task cannot double.
    """
    created: list[dict] = []
    bot_repo = world.get(BotRepository)

    from agentclaw.community.core.repository.protocols.devices import (
        DeviceBindingRepository,
    )
    from tests.community.factories.access import make_staff_user
    make_staff_user(world, user_id=OWNER)
    binding_repo = world.get(DeviceBindingRepository)

    def create_bot(_self, **kwargs):
        existing = bot_repo.get_by_id_and_entity(
            kwargs["bot_id"], kwargs.get("entity_id") or kwargs["user_id"]
        )
        if existing is not None:
            return existing
        # A local (pathlib) device binding: what allocation would have given
        # the bot — the identity apply resolves it, and its writes land under
        # the local workspace root the test points at a temp dir.
        bind_id = binding_repo.insert_binding(
            entity_id=kwargs.get("entity_id") or kwargs["user_id"],
            entity_type="staff",
            device_id=f"dev_{kwargs['bot_id']}",
            device_provider="local", env="dev", device_props={},
            status="ACTIVE", apply_reason="e2e stand-in",
            applied_by=kwargs["user_id"],
        )
        record = bot_repo.insert(
            {
                "bot_id": kwargs["bot_id"],
                "bot_name": kwargs.get("bot_name") or kwargs["bot_id"],
                "bot_desc": kwargs.get("bot_desc"),
                "owner_id": kwargs["user_id"],
                "owner_name": kwargs["user_id"],
                "entity_id": kwargs.get("entity_id") or kwargs["user_id"],
                "entity_type": kwargs.get("entity_type") or "staff",
                "creator_id": kwargs["user_id"],
                "bot_type": kwargs.get("bot_type") or "personal",
                "status": "ACTIVE",
                "active_engine": kwargs.get("engine_type") or "openclaw",
                "binding_id": bind_id,
            }
        )
        created.append(record)
        return record

    from agentclaw.community.api.bot_service import BotServiceProtocol
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    bind_overrides(
        world, BotServiceProtocol, {"create_bot": create_bot},
        also_bind=(BotService,),
    )
    return created


class _TaskQueuePump(threading.Thread):
    """The endpoint suite's ``_Worker`` on its own thread: claim, run the
    real creation and apply handlers, settle the row. A thread, not a
    coroutine, because the creation handler and its apply phases bridge
    their internal async bits with ``asyncio.run`` — legal on a thread
    with no loop, fatal inside the swarm's already-running one. Reschedules
    come back immediately — the job's five-second poll delay is latency,
    not sequencing."""

    _WORKER = "bot-e2e-worker"

    def __init__(self, world) -> None:
        super().__init__(daemon=True)
        self._world = world
        self._stopped = threading.Event()
        self._repo = world.get(TaskQueueRepositoryProtocol)

    def _dispatch(self, task):
        from agentclaw.community.core.bot_config_manifest.create_job import (
            BotCreateWithManifestHandler,
        )
        if task.task_type == CREATE_JOB_TASK_TYPE:
            return self._world.get(BotCreateWithManifestHandler).handle(task.payload)
        if task.task_type == APPLY_TASK_TYPE:
            self._world.get(ApplyRunServiceProtocol).run_apply_task(
                task.payload
            )
            return Complete()
        return None

    def _settle(self, task, outcome) -> None:
        if outcome is None or isinstance(outcome, Reschedule):
            self._repo.reschedule(
                task_id=task.id,
                worker_id=self._WORKER,
                delay_seconds=0 if outcome is not None else 3600,
            )
        elif isinstance(outcome, Fail):
            self._repo.fail(task_id=task.id, worker_id=self._WORKER, error=outcome.error)
        else:
            self._repo.complete(task_id=task.id, worker_id=self._WORKER)

    def turn(self) -> list:
        claimed = self._repo.claim_batch(
            worker_id=self._WORKER,
            env=get_current_env(),
            app=DEFAULT_APP,
            limit=10,
            lease_seconds=300,
        )
        settled = []
        for task in claimed:
            outcome = self._dispatch(task)
            self._settle(task, outcome)
            settled.append((task.task_type, outcome))
        return settled

    def run(self):
        while not self._stopped.is_set():
            if self.turn():
                continue
            time.sleep(0.01)

    def stop_and_join(self, timeout: float = 30):
        self._stopped.set()
        self.join(timeout)
        assert not self.is_alive(), "the task-queue pump thread did not stop"


class TestBotKindEndToEnd:

    @pytest.mark.asyncio
    async def test_bot_agent_runs_a_real_swarm_round(
            self, world, swarm_db, tmp_path, monkeypatch):
        from agentclaw.community.core.config.provider import load_config
        load_config().user_config["aidesktop_root"] = str(tmp_path)

        from agentclaw.community.api.bot_service import BotServiceProtocol
        from agentclaw.community.core.skill_center.skill_set_service_factory_protocol import (
            SkillSetServiceFactoryProtocol,
        )
        from agentclaw.community.core.service_bot.services.baas_service import (
            BaasService,
        )
        from agentclaw.community.plugin_api.passport import PassportPlugin

        created = _stand_in_for_provisioning(world)
        ws = _ScriptedEngineWs(reply='{"answer": "survey ok"}')
        platform = AvernetBotPlatform(
            manifest_seam=world.get(ManifestCreationSeam),
            bot_service=world.get(BotServiceProtocol),
            passport_plugin=world.get(PassportPlugin),
            skill_set_factory=world.get(SkillSetServiceFactoryProtocol),
            bot_repo=world.get(BotRepository),
            apply_service=world.get(ApplyRunServiceProtocol),
            space_context=world.get(BusinessSpaceContextProtocol),
            baas_service=world.get(BaasService),
        )
        endpoints = _patch_engine_edges(monkeypatch, platform, ws)

        script = [
            _spawn_decision(("surveyor", "survey the incident")),
            _wait_decision("surveyor"),
            _finish_decision("e2e-bot-done"),
        ]
        service, worker, repos = _build_swarm(swarm_db, script, platform)
        req = SubmitRunRequest(
            tenant=TENANT,
            goal="e2e: a bot-kind agent answers through the platform",
            owner_id=OWNER,
            agents=(
                dm.AgentSpec(
                    name="surveyor", role="swarm surveyor persona",
                    instructions="Be a concise surveyor.",
                    kind=dm.AgentKind.BOT,
                ),
            ),
        )
        run_id = await service.submit_async(req)

        # The pump thread settles the creation job while the swarm's
        # slices run on the loop; the subagent's 1s READY poll yields in
        # between, and a later poll sees READY.
        pump = _TaskQueuePump(world)
        pump.start()

        async def _drive():
            run = None
            for _ in range(400):
                await worker.run_once()
                run = repos["run"].get(dm.RunId(run_id), TENANT)
                if run is not None and run.status is not dm.RunStatus.RUNNING:
                    return run
                await asyncio.sleep(0.01)
            return run

        try:
            run = await asyncio.wait_for(_drive(), timeout=60)
        finally:
            pump.stop_and_join()

        assert run is not None, "run row vanished"
        assert run.status is dm.RunStatus.COMPLETED
        assert run.termination_reason is dm.TerminationReason.FINISH
        assert run.final_answer == "e2e-bot-done"

        agents = repos["agent"].list_by_run(dm.RunId(run_id), TENANT)
        assert [a.name for a in agents] == ["surveyor"]
        assert all(a.status is dm.AgentStatus.COMPLETED for a in agents)

        results = repos["result"].list_by_run(dm.RunId(run_id), TENANT)
        assert len(results) == 1
        assert results[0].payload == {"answer": "survey ok"}

        seam = world.get(ManifestCreationSeam)
        assert len(created) == 1
        bot = created[0]
        # The platform identity is the SPAWN's identity per attempt: the
        # runtime-minted agent_id plus the in-slice attempt index (D-J)
        # — a same-slice retry would mint a fresh persona instead of
        # colliding with the torn-down attempt's row. The e2e asserts
        # the exact derivation through the canonical helper against the
        # agent row the swarm itself just persisted.
        spawned_agent = agents[0].agent_id
        expected_id = "agent_swarm_" + spawn_digest(run_id, spawned_agent, 1, 16)
        assert bot["bot_id"] == expected_id
        assert bot["status"] == "ACTIVE"
        assert bot["owner_id"] == OWNER
        assert bot["bot_desc"] == "swarm surveyor persona"
        assert bot["bot_name"] == f"surveyor-{expected_id[12:20]}"

        job = seam.find_job(entity_id=OWNER, bot_id=expected_id)
        assert job is not None and job.status is TaskStatus.SUCCEEDED

        report = world.get(ApplyRunServiceProtocol).last_apply(
            entity_id=OWNER, bot_id=expected_id,
        )
        assert report is not None
        assert report.trigger == CREATE_ON_CONTAINER_TRIGGER
        assert report.status is ApplyStatus.SUCCEEDED

        # The chat leg: one round per leg — the reset of the engine-served
        # session first, then the message the orchestrator spawned, on the
        # same key, to the run's owner. The swarm passes no session key,
        # so both frames address the engine-served default conversation,
        # and the reset is what makes each leg fresh.
        assert [f["method"] for f in ws.sent_frames] == [
            "sessions.reset", "chat.send",
        ]
        reset_frame, chat_frame = ws.sent_frames
        assert "survey the incident" in chat_frame["params"]["message"]
        assert endpoints == [(expected_id, OWNER)]
        assert reset_frame["params"]["key"] == "agent:main:default"
        assert chat_frame["params"]["sessionKey"] == "agent:main:default"
        assert ws.closed