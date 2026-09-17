"""TDD tests for the SUBMIT trajectory gate (REQ-6).

P3 item 5 of the task-trajectory spec. The SUBMIT gate is the **first timeline
segment** — it fires once at/after the ``task_info`` persist point inside
``TaskService.execute`` (the spec's cited ``engine.py:351-357`` is STALE —
it's the engine constructor, not a gate; see ``plan.md`` Spec clarifications
#4). The real persist happens at ``task_service.py``'s ``self._task_info_repo
.insert(record)`` inside ``execute``; the SUBMIT emission sits right after
that block and before ``initialize_graph``, so it covers **all** execute
branches (workflow / yaml / bbs / dynamic) — the branch dispatch happens
below it.

The emitted row must carry (per REQ-6 / spec clarification):

    action_type   = ``TrajectoryActionType.SUBMIT`` ("submit") — a trajectory
                    action-type, **not** ``NodeAction`` (``NodeAction`` enum is
                    untouched; ``submit`` is absent from it).
    action_input  = ``task_spec_digest`` = SHA-256 over the JSON-serialised
                    submitted ``TaskSpec`` (``to_dict()`` with ``sort_keys=True``);
                    64-hex. Mirrors the PLAN gate's SHA-256-over-stringified-form
                    convention (P3-2).
    ext_info      = ``{"schema_v": 1, "source": <source_type>, "task_type":
                    <task_type>, "owner_user_id": <...>, "owner_bot_id": <...>,
                    "submitted_at": <int ms>}`` — values from the submitted
                    ``TaskInfo``; ``submitted_at`` = the persist timestamp
                    ``int(time.time()*1000)``.
    status_from   = ``None`` (no prior status — first event of the timeline).
    status_to     = ``"PENDING"`` (the initial persisted status of a task).
    attempt       = ``0`` (no harness retry at submit time).
    error_*       = ``None`` (success; persist IntegrityError short-circuits
                    ``execute`` before this helper runs).
    action_result = ``"success"``.

Invariants the tests pin (cross-cutting with the task constraints):
    * ``submit`` is **NOT** a ``NodeAction``; the root node's ``action_log``
      never contains a ``submit`` action — the trajectory row goes to the
      ``_TrajRepo`` fake via ``emit_submit_trajectory`` → ``emit_trajectory_event``
      direct-INSERT only (no ``append_action_event`` / ``_log_action``).
    * ``NodeAction`` enum / ``append_action_event`` / ``task_action_log``
      untouched.
    * Zero intrusion: ``execute`` returns success even when the trajectory
      repo raises (swallow guarantee, 决策 #14); the submit persist/dispatch
      main logic is NOT swallowed — only the trajectory assembly (digest /
      ext_info) is swallowed inside the helper.
    * All execute branches (workflow/yaml/bbs/dynamic) emit one ``submit`` row.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

import pytest

from agentclaw.community.core.task.domain.models import (
    Status,
    TaskSourceType,
    TaskOpResult,
    TaskType,
)
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance,
    RequestContext,
    RequestGoal,
    RequestMetadata,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


# ---------------------------------------------------------------------------
# Shared helpers / fakes
# ---------------------------------------------------------------------------


def _run(coro):
    """Sync wrapper to drive async ``execute`` in unit tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


class _TrajRepo:
    """Fake trajectory repo — captures every ``insert_event`` call (no SQLite)."""

    def __init__(self) -> None:
        self.records: list[TrajectoryEventRecord] = []
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        self.records.append(record)
        return record


class _CaseTaskService(TaskService):
    """Test subclass isolating the SUBMIT gate from per-branch internals.

    The SUBMIT gate sits ABOVE the ``task_type`` branch in ``execute``, so it
    fires for every branch. To keep the SUBMIT tests branch-agnostic, the
    workflow/yaml/bbs branches are stubbed to a no-op success — the real
    branch work (engine ``_runner.start_run`` / ``start_coop_group`` /
    ``_hung_and_escalate``) is orthogonal to the trajectory emission. The
    default (dynamic) branch is left alone: it schedules ``on_execute`` in
    the background; ``drain_background`` swallows any background error, so the
    already-fired SUBMIT row is what the test asserts on.
    """

    async def _run_workflow(self, task_id, request, task_info, run_id):
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    async def _run_yaml(self, task_id, request, task_info, run_id):
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)

    async def _run_bbs(self, task_id, request, task_info, run_id):
        return TaskOpResult(task_id=task_id, success=True, run_id=run_id)


def _request(
    *,
    task_type=TaskType.DYNAMIC,
    source_type=TaskSourceType.API,
    task_id="submit-tid",
    extra_cfg=None,
) -> TaskInfoRequest:
    cfg: dict = {"task_type": task_type}
    if extra_cfg:
        cfg.update(extra_cfg)
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(
                objective="o",
                acceptances=[RequestAcceptance(id="ac1", acceptance="acc")],
            ),
        ),
        source_type=source_type,
        owner_user_id="U1",
        owner_bot_id="B1",
        execution_config=cfg,
    )


def _exec(facade, request):
    """execute (fire-and-forget) → drain_background so the background task
    settles (test-determinism seam; same event loop). Exceptions from the
    background ``on_execute`` are swallowed by ``drain_background`` (returns
    ``return_exceptions=True``) — the SUBMIT row already fired by then."""

    async def _go():
        r = await facade.execute(request)
        await facade.drain_background()
        return r

    return asyncio.new_event_loop().run_until_complete(_go())


def _submit_records(repo: _TrajRepo) -> list[TrajectoryEventRecord]:
    return [r for r in repo.records if r.action_type == "submit"]


def _service(traj_repo, *, task_info_repo=None, task_id="submit-tid"):
    return _CaseTaskService(
        TaskGraphService(),
        task_info_repo=task_info_repo,
        task_id_provider=lambda: task_id,
        trajectory_repo=traj_repo,
    )


# ---------------------------------------------------------------------------
# 1. Core SUBMIT emission — dynamic branch (covers the "external" / API source)
# ---------------------------------------------------------------------------


class TestSubmitGateEmission:
    """REQ-6: ``execute`` emits exactly one ``submit`` trajectory row with the
    required fields, anchored on the task_id (root node)."""

    def test_dynamic_emits_exactly_one_submit_row(self):
        repo = _TrajRepo()
        svc = _service(repo)
        result = _exec(svc, _request(task_type=TaskType.DYNAMIC, source_type=TaskSourceType.API))

        # execute succeeds (the submit persist succeeded; any background
        # on_execute error is swallowed by drain_background and does not
        # change the TaskOpResult returned from execute — fire-and-forget).
        assert result.task_id == "submit-tid"
        assert result.success is True

        submit_rows = _submit_records(repo)
        assert len(submit_rows) == 1, [
            (r.action_type, r.action_result) for r in repo.records
        ]
        rec = submit_rows[0]
        # action_type / node_id anchored on the task root (submit fires before
        # any child nodes exist, so the root is the only candidate anchor).
        assert rec.action_type == "submit"
        assert rec.node_id == "submit-tid"
        assert rec.task_id == "submit-tid"
        # status: None → PENDING (initial); attempt 0; success → no error_*
        assert rec.status_from is None
        assert rec.status_to == "PENDING"
        assert rec.attempt == 0
        assert rec.action_result == "success"
        assert rec.error_type is None
        assert rec.error_msg is None
        # action_input = task_spec_digest, 64-hex (SHA-256 over TaskSpec)
        assert rec.action_input is not None
        assert len(rec.action_input) == 64
        int(rec.action_input, 16)  # raises if not valid hex

    def test_submit_ext_info_carries_all_required_fields(self):
        repo = _TrajRepo()
        svc = _service(repo)
        before = int(time.time() * 1000)
        _exec(svc, _request(
            task_type=TaskType.DYNAMIC, source_type=TaskSourceType.API
        ))
        after = int(time.time() * 1000)

        rec = _submit_records(repo)[0]
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        # emitter wraps the caller's ext_info in ``{"schema_v": 1, **ext_info}``
        assert payload["schema_v"] == 1
        assert payload["source"] == "api"          # source_type.value
        assert payload["task_type"] == "dynamic"   # TaskType.DYNAMIC.value
        assert payload["owner_user_id"] == "U1"
        assert payload["owner_bot_id"] == "B1"
        # submitted_at = the persist timestamp (int ms); within [before, after]
        assert isinstance(payload["submitted_at"], int)
        assert before <= payload["submitted_at"] <= after

    def test_submit_task_spec_digest_matches_sha256_of_serialized_spec(self):
        """``action_input`` MUST equal ``SHA-256(json.dumps(spec.to_dict(),
        sort_keys=True))`` — mirrors the PLAN digest convention (P3-2:
        SHA-256 over a stringified/serialized form)."""
        from agentclaw.community.core.task.domain.models import (
            AcceptanceCriteria, Context, Goal, Metadata, TaskInfo, TaskSpec,
        )

        repo = _TrajRepo()
        svc = _service(repo)
        req = _request(task_type=TaskType.DYNAMIC, source_type=TaskSourceType.API)
        _exec(svc, req)

        rec = _submit_records(repo)[0]
        # Reconstruct the submitted TaskInfo exactly as execute does (via
        # ``request.to_task_info(task_id)``) and recompute the expected digest.
        task_info = req.to_task_info("submit-tid")
        expected = hashlib.sha256(
            json.dumps(task_info.task_spec.to_dict(), sort_keys=True,
                      ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        assert rec.action_input == expected

    def test_submit_ext_info_internal_source_is_bot_or_coop_group(self):
        """``source_type`` value flows through verbatim — ``TaskSourceType.BOT``
        → ``ext_info.source == "bot"`` (covers the "internal" submit source)."""
        repo = _TrajRepo()
        svc = _service(repo, task_id="bot-tid")
        _exec(svc, _request(
            task_type=TaskType.DYNAMIC, source_type=TaskSourceType.BOT,
            task_id="bot-tid",
        ))
        rec = _submit_records(repo)[0]
        payload = json.loads(rec.ext_info)
        assert payload["source"] == "bot"


# ---------------------------------------------------------------------------
# 2. All execute branches fire SUBMIT — workflow / yaml / bbs / dynamic
# ---------------------------------------------------------------------------


class TestSubmitGateBranchCoverage:
    """REQ-6 acceptance: 现有各执行分支 (workflow/yaml/bbs/外部) 均覆盖 —
    each branch's trajectory timeline[0].action_type == "submit"."""

    @pytest.mark.parametrize("task_type,extra_cfg,branch_label", [
        (TaskType.DYNAMIC, None, "dynamic (external API source)"),
        (TaskType.WORKFLOW, {"workflow": ["c1", "c2"]}, "workflow"),
        (TaskType.YAML, {"yaml": "steps: []", "participant_bot_ids": []}, "yaml"),
        (TaskType.BBS, None, "bbs"),
    ])
    def test_each_branch_emits_one_submit_row(self, task_type, extra_cfg, branch_label):
        repo = _TrajRepo()
        svc = _service(repo, task_id=f"tid-{branch_label.split()[0]}")
        req = _request(
            task_type=task_type,
            task_id=f"tid-{branch_label.split()[0]}",
            extra_cfg=extra_cfg,
        )
        result = _exec(svc, req)

        submit_rows = _submit_records(repo)
        assert len(submit_rows) == 1, (
            f"branch={branch_label} expected exactly 1 submit row, got "
            f"{len(submit_rows)}; all records: "
            f"{[(r.action_type, r.action_result) for r in repo.records]}"
        )
        rec = submit_rows[0]
        assert rec.action_type == "submit"
        assert rec.status_to == "PENDING"
        assert rec.attempt == 0
        assert rec.action_input is not None and len(rec.action_input) == 64
        # execute result is success for the stubbed branches (workflow/yaml/bbs)
        # and for the dynamic branch (fire-and-forget returns success before
        # the background task is awaited).
        assert result.success is True, f"branch={branch_label} execute result: {result}"

    def test_timeline_first_action_is_submit(self):
        """The ``submit`` row is first in the trajectory timeline for any task
        (REQ-6: ``timeline[0].action_type == "submit"``). At emit-level this
        means: among ALL trajectory rows captured for the task, the ``submit``
        row's ``gmt_create`` is ≤ every other row's (it fires before
        ``initialize_graph`` / dispatch / plan / execute). With only the submit
        gate wired (P3-5), this test pins the emit-order invariant additively;
        later gates (PLAN/DISPATCH/etc.) fire downstream and later in wall time.
        """
        repo = _TrajRepo()
        svc = _service(repo)
        _exec(svc, _request())

        records = repo.records
        assert records, "no trajectory rows emitted"
        # the submit row is the first captured row (it fires at the top of
        # execute, before initialize_graph schedules any other gate)
        assert records[0].action_type == "submit", [
            r.action_type for r in records
        ]


# ---------------------------------------------------------------------------
# 3. Fire-point placement — AT or AFTER the task_info persist; fires even
#    when task_info_repo is None (lightweight path) and when it's present
# ---------------------------------------------------------------------------


class TestSubmitGateFirePoint:
    """REQ-6: the SUBMIT gate fires at/after the persist point — covering both
    the persist-repo-present (prod) and the ``None`` repo (lightweight/in-memory)
    paths. A persist ``IntegrityError`` short-circuits ``execute`` (returns
    failure) and no SUBMIT row lands (the task was never persisted)."""

    def test_submit_fires_when_task_info_repo_is_none(self):
        """Lightweight path (``task_info_repo=None``): no DB persist happens,
        but the task_info is materialized in memory and the SUBMIT event still
        fires to mark the submission (REQ-6 covers in-memory submissions too;
        the spec's "so the task exists" is trivially satisfied by the
        in-memory task_info)."""
        repo = _TrajRepo()
        svc = _service(repo, task_info_repo=None)
        _exec(svc, _request())
        assert len(_submit_records(repo)) == 1

    def test_submit_fires_when_task_info_repo_is_present(self):
        """Prod path (``task_info_repo`` is a real repo): the persist happens
        BEFORE the SUBMIT gate fires, so by the time the row lands the task
        exists in the DB. Verified by asserting the persisted task_info row
        coexists with the trajectory row."""
        from contextlib import contextmanager

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from agentclaw.community.core.base import Base
        import agentclaw.community.core.task.repository.models  # noqa: F401
        import agentclaw.community.core.task_queue.repository.models  # noqa: F401
        from agentclaw.community.core.repository.implementations.task.task_info_repository import (
            TaskInfoRepository,
        )

        class _SqliteDB:
            def __init__(self, engine):
                self._f = sessionmaker(bind=engine, autoflush=False)

            @contextmanager
            def orm_session(self):
                db = self._f()
                try:
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(eng)
        info_repo = TaskInfoRepository(_SqliteDB(eng))

        traj_repo = _TrajRepo()
        svc = _service(traj_repo, task_info_repo=info_repo, task_id="persisted-tid")
        result = _exec(svc, _request(task_id="persisted-tid"))
        assert result.success is True

        # the task_info row persisted (PENDING)
        row = info_repo.get("persisted-tid")
        assert row is not None
        assert row.status is Status.PENDING
        # AND exactly one submit trajectory row fired (after the persist)
        assert len(_submit_records(traj_repo)) == 1

    def test_submit_does_not_fire_on_persist_integrity_error(self):
        """Decision #14 boundary: the submit persist main logic is NOT
        swallowed. If ``insert`` raises ``IntegrityError``, ``execute``
        short-circuits with a failure result BEFORE the SUBMIT gate — no
        trajectory row lands for a task that wasn't actually persisted."""
        from contextlib import contextmanager

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from agentclaw.community.core.base import Base
        import agentclaw.community.core.task.repository.models  # noqa: F401
        import agentclaw.community.core.task_queue.repository.models  # noqa: F401
        from agentclaw.community.core.repository.implementations.task.task_info_repository import (
            TaskInfoRepository,
        )
        from agentclaw.community.core.task.repository.types import TaskInfoRecord

        class _SqliteDB:
            def __init__(self, engine):
                self._f = sessionmaker(bind=engine, autoflush=False)

            @contextmanager
            def orm_session(self):
                db = self._f()
                try:
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(eng)
        info_repo = TaskInfoRepository(_SqliteDB(eng))

        # pre-insert the same task_id so the execute insert hits uk_task_id
        info_repo.insert(TaskInfoRecord(
            id=0, task_id="persisted-tid", source_type="api", owner_user_id="U1",
            owner_bot_id="B1", execution_config={"task_type": "dynamic"},
            task_spec={"metadata": {"task_id": "persisted-tid"}}, status=Status.PENDING,
        ))

        traj_repo = _TrajRepo()
        svc = _service(traj_repo, task_info_repo=info_repo, task_id="persisted-tid")
        result = _exec(svc, _request(task_id="persisted-tid"))
        # execute returns failure (persist failed)
        assert result.success is False
        assert result.error is not None
        # AND no submit row landed — the task was never persisted
        assert _submit_records(traj_repo) == [], (
            "SUBMIT should not fire on persist IntegrityError (task never existed)"
        )


# ---------------------------------------------------------------------------
# 4. Zero intrusion — submit persist/dispatch main logic NOT swallowed
# ---------------------------------------------------------------------------


class TestSubmitGateZeroIntrusion:
    """决策 #14: the trajectory EMISSION is the only thing swallowed+WARNING
    (inside ``emit_trajectory_event``); the submit persist/dispatch main logic
    is NOT swallowed. A broken trajectory repo must not break ``execute``."""

    def test_execute_completes_when_trajectory_repo_raises(self, caplog):
        """Global swallow guarantee (决策 #14): even if the trajectory repo
        raises inside ``insert_event``, ``execute`` still returns success
        (fire-and-forget observational 旁路) and the emitter logs WARNING."""

        class _BoomRepo:
            def insert_event(self, record):
                raise RuntimeError("trajectory submit boom")

        svc = _service(_BoomRepo())
        with caplog.at_level(logging.WARNING, logger="task.trajectory"):
            result = _exec(svc, _request())
        # execute completes and returns success (no exception bubbled)
        assert result.success is True
        # emitter swallowed the exception and logged WARNING (observable)
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "task.trajectory" in r.name
        ]
        assert any("submit boom" in r.getMessage() for r in warnings), (
            [r.getMessage() for r in caplog.records]
        )

    def test_submit_does_not_write_task_action_log(self):
        """``submit`` is a trajectory action-type, NOT a ``NodeAction``. The
        root node's ``action_log`` (the in-memory ``append_action_event`` path)
        never contains a ``submit`` action — the trajectory row goes through
        ``emit_submit_trajectory`` → ``emit_trajectory_event`` direct-INSERT
        only. Pins the ``NodeAction`` / ``append_action_event`` / ``task_action_log``
        untouched invariant."""
        graph = TaskGraphService()
        repo = _TrajRepo()
        svc = _CaseTaskService(
            graph,
            task_info_repo=None,
            task_id_provider=lambda: "naction-tid",
            trajectory_repo=repo,
        )
        _exec(svc, _request(task_id="naction-tid"))

        # the submit trajectory row fired
        assert len(_submit_records(repo)) == 1
        # the root node's action_log has NO submit action
        root = graph._get_node(graph._graphs["naction-tid"], "naction-tid")
        action_log = root.run_info.action_log
        submit_actions = [
            ev for ev in action_log if ev.action.value == "submit"
        ]
        assert submit_actions == [], (
            "submit is a TrajectoryActionType, never a NodeAction; "
            f"action_log should have no submit entry, got {submit_actions}"
        )


# ---------------------------------------------------------------------------
# 5. submit is a TrajectoryActionType — never NodeAction (invariant pin)
# ---------------------------------------------------------------------------


class TestSubmitIsTrajectoryActionTypeNotNodeAction:
    """REQ-6 invariant: ``submit`` is a member of ``TrajectoryActionType``,
    NOT of ``NodeAction``. The emitted row's ``action_type`` is the
    ``TrajectoryActionType.SUBMIT`` value (``"submit"``); the ``NodeAction``
    enum is unchanged and has no ``submit`` member."""

    def test_submit_in_trajectory_action_type(self):
        from agentclaw.community.core.task.task_trajectory.models import (
            TrajectoryActionType,
        )
        assert TrajectoryActionType.SUBMIT.value == "submit"
        assert "submit" in {a.value for a in TrajectoryActionType}

    def test_submit_not_in_node_action(self):
        from agentclaw.community.core.task.domain.models import NodeAction
        node_values = {a.value for a in NodeAction}
        assert "submit" not in node_values, (
            "NodeAction must NOT gain a submit member (REQ-6: submit is a "
            f"trajectory action-type only); got {node_values}"
        )
