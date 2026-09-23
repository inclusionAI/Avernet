"""task_center line-coverage gap tests.

Closes the missing lines recorded for the eight ``task_center`` modules in
``/tmp/task_cov_baseline.json``:

* ``task_service_support`` / ``recovery``: hostile-collaborator fallbacks.
* ``relay``: RelayCoordinator guards + diagnostic emitters (partial leases,
  legacy digests, in-flight reservations) and their swallow-and-log paths.
* ``task_service``: facade edges — static-plan materialization error mapping,
  converge/session callbacks, manager_worker CloudEvent handling, background
  task lifecycle, anniversary enrichment, node info writes and ``run_execute``.
* ``task_service_execution`` / ``task_service_queries``: execution-branch
  failures, the BBS delegation and dashboard/list enrichment projection.
* ``task_service_relay`` / ``task_service_relay_dispatch``: relay event guards
  (foreign holders, stale plans, hostile deliveries) and BBS report guards.

Fakes are defined locally; relay flows reuse the shared builders exported by
``tests/.../task_center/test_relay_execution.py`` (imported, never modified).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Relation,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance,
    RequestContext,
    RequestGoal,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.domain.models import TaskSourceType
from agentclaw.community.core.task.repository.types import (
    BbsTaskOverviewRecord,
    TaskInfoRecord,
    TaskNodeRunInfoRecord,
)
from agentclaw.community.core.task.task_center.recovery import TaskRecoveryWorker
from agentclaw.community.core.task.task_center.relay import (
    RelayCoordinator,
    _diagnostic_text,
    emit_relay_callback_error,
    emit_relay_callback_success,
    emit_relay_event,
    relay_attempt,
)
from agentclaw.community.core.task.task_center.task_service import TaskService, run_execute
from agentclaw.community.core.task.task_center.task_service_support import (
    build_submit_trajectory_event_kwargs,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from tests.community.core.task.task_center.test_relay_execution import (
    _DiscoverTwo,
    _Settings,
    _accepted,
    _child_spec,
    _plan_and_select,
    _request,
    _run,
    _service,
)


# ===== local helpers (fixture/fake defined in place; no shared fixtures) =====

def _info(task_id: str = "t-init", **config):
    """A plain (centralized) TaskInfo for direct ``initialize_graph`` use."""
    from agentclaw.community.core.task.domain.models import TaskInfo

    return TaskInfo(
        task_id=task_id,
        task_spec=TaskSpec(
            context=Context(background="bg", title="T"),
            goal=Goal(objective="调研目标", acceptances=[]),
        ),
        source_type="bot",
        owner_bot_id="b1",
        execution_config={"MAX_DEPTH": 2, **config},
    )


def _typed_request(task_type: str, task_id: str = "t-typed", **xec) -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            context=RequestContext(background="bg", title="T"),
            goal=RequestGoal(
                objective="o",
                acceptances=[RequestAcceptance(id="a", acceptance="d")],
            ),
        ),
        source_type=TaskSourceType.API,
        owner_user_id="u1",
        owner_bot_id="b1",
        execution_config={"task_type": task_type, **xec},
    )


def _build_service(graph=None, *, relay_settings: bool = True, **kw):
    """Local mirror of the relay tests' ``_service`` builder, with open kwargs."""
    graph = graph or TaskGraphService()
    service = TaskService(
        graph,
        discover=kw.pop("discover", _DiscoverTwo()),
        task_settings=_Settings(relay_settings),
        task_id_provider=kw.pop("task_id_provider", lambda: "relay-task"),
        **kw,
    )
    return service, graph


def _grant_root_turn(service, event_id: str = "exec-1", payload=None) -> str:
    """Execute the relay task and report an accepted execution (turn granted)."""
    _run(service.execute(_request()))
    execution = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id=event_id,
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=payload if payload is not None else _accepted("done"),
        )
    )
    return execution["relay_turn"]


def _plan_next(service, turn: str, event_id: str, payload: dict):
    return _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id=event_id,
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="存在下一棒缺口",
            payload=payload,
        )
    )


def _publish_bbs_target(service):
    """Grant a turn, plan the next baton and publish it to the BBS market."""
    turn = _grant_root_turn(service)
    planned = _plan_next(
        service,
        turn,
        "plan-bbs",
        {"gaps": ["补齐市场研究 gap"], "next_task_spec": _child_spec()},
    )
    target = planned["target_node_id"]
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=target,
            event_type="DISPATCH_RESULT",
            event_id="miss-bbs",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="普通候选无法覆盖，发布 BBS",
            failure_reason="无匹配候选",
            payload={"outcome": "MISS", "miss_reason": "no_candidates"},
        )
    )
    return target


class _BotSvc:
    """BotService fake: scriptable pair lookup + per-bot-id lookup."""

    def __init__(self, *, fail_pairs: bool = False, fail_by_id: bool = False):
        self.fail_pairs = fail_pairs
        self.fail_by_id = fail_by_id

    def list_bots_by_owner_bot_pairs(self, *, pairs, page=1, page_size=20):
        del page, page_size
        if self.fail_pairs:
            raise RuntimeError("pair lookup down")
        items = [
            "junk-not-a-dict",  # exercises the isinstance guard
            {"bot_id": "b-ok", "owner_id": "u1", "bot_name": "OK Bot"},
            {"bot_id": "b1", "owner_id": "u1", "bot_name": "Name1"},
        ]
        return {"items": [item for item in items]}

    def get_bot_by_id(self, bot_id: str):
        if self.fail_by_id:
            raise RuntimeError("bot id lookup down")
        return {"bot_id": bot_id, "owner_id": "u1", "bot_name": "OK Bot"}


class _StaffDept:
    def __init__(self, *, raise_profile: bool = False):
        self.raise_profile = raise_profile

    def get_profile_by_work_no(self, *, work_no: str):
        if self.raise_profile:
            raise RuntimeError("staff dept down")
        return SimpleNamespace(nick_name=f"User-{work_no}")


# =====================================================================
# task_service_support: trajectory submit kwargs must never block submit
# =====================================================================


def test_submit_trajectory_kwargs_survive_hostile_task_spec_and_config() -> None:
    class _BoomSpec:
        def to_dict(self):
            raise RuntimeError("serialize failed")

    class _BoomConfig:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("config failed")

    info = SimpleNamespace(
        task_spec=_BoomSpec(),
        execution_config=_BoomConfig(),
        source_type="bot",
        owner_user_id="u1",
        owner_bot_id="b1",
    )

    kwargs = build_submit_trajectory_event_kwargs(info, submitted_at_ms=123)

    assert kwargs["action_input"] is None  # digest fell back to None
    assert kwargs["ext_info"]["task_type"] is None  # task_type fell back to None
    assert kwargs["ext_info"]["owner_user_id"] == "u1"
    assert kwargs["status_to"] == Status.PENDING
    assert kwargs["now_ms"] == 123


# =====================================================================
# recovery: one-shot worker skips vanished graphs and logs resume failures
# =====================================================================


def test_recovery_worker_skips_unleased_and_missing_graphs_and_logs_failures() -> None:
    class _Repo:
        def __init__(self):
            self.leased: list[tuple[str, str]] = []
            self.released: list[str] = []

        def list_recoverable(self, *, limit=100):
            del limit
            return ["t-no-lease", "t-no-graph", "t-boom", "t-fine"]

        def acquire_lease(self, task_id, *, instance_id, lease_seconds):
            del lease_seconds
            if task_id == "t-no-lease":
                return False
            self.leased.append((task_id, instance_id))
            return True

        def load_graph(self, task_id):
            return None if task_id == "t-no-graph" else object()

        def release_lease(self, task_id, *, instance_id):
            self.released.append(task_id)
            assert instance_id == "worker-1"

    async def _resume(task_id: str) -> None:
        if task_id == "t-boom":
            raise RuntimeError("resume exploded")

    repo = _Repo()
    worker = TaskRecoveryWorker(repo, _resume, instance_id="worker-1")

    recovered = _run(worker.recover_once())

    assert recovered == ["t-fine"]  # missing graph skipped; failure logged, not fatal
    assert sorted(repo.released) == ["t-boom", "t-fine", "t-no-graph"]
    assert ("t-fine", "worker-1") in repo.leased


# =====================================================================
# relay.RelayCoordinator lease guards
# =====================================================================


def test_coordinator_rejects_grant_on_non_relay_graph() -> None:
    graph_service = TaskGraphService()
    graph_service.initialize_graph(_info("t-plain"))
    coordinator = RelayCoordinator(graph_service)

    with pytest.raises(TaskStateError, match="not in relay mode"):
        coordinator.grant("t-plain", "t-plain", "b1")


def test_coordinator_retry_grant_rejects_foreign_holder() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)

    coordinator = RelayCoordinator(graph_service)
    assert (
        coordinator.grant(
            "relay-task", "relay-task", "hijacker", retry_event=True
        )
        is None
    )


def test_coordinator_grant_rejects_second_holder_while_turn_active() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)

    coordinator = RelayCoordinator(graph_service)
    with pytest.raises(TaskStateError, match="already granted"):
        coordinator.grant("relay-task", "relay-task", "hijacker")


def test_require_accepts_legacy_single_digest_token() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)
    legacy_token = "legacy-bearer-token"
    graph_service.update_task_graph_info(
        "relay-task",
        TaskGraphPatch(
            extend_props_patch={
                "relay_turn": {
                    "node_id": "relay-task",
                    "holder_id": "main-bot",
                    "status": "GRANTED",
                    "expires_at_ms": int(time.time() * 1000) + 60_000,
                    "token_digest": hashlib.sha256(
                        legacy_token.encode("utf-8")
                    ).hexdigest(),
                }
            }
        ),
    )

    coordinator = RelayCoordinator(graph_service)
    assert (
        coordinator.require("relay-task", "relay-task", "main-bot", legacy_token)
        == "relay-task"
    )


def test_consume_rejects_mismatched_token() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)
    coordinator = RelayCoordinator(graph_service)

    with pytest.raises(TaskStateError, match="cannot be consumed"):
        coordinator.consume("relay-task", "relay-task", "main-bot", "wrong-token")


def test_renew_expired_returns_none_while_turn_is_active() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)

    assert (
        RelayCoordinator(graph_service).renew_expired(
            "relay-task", "relay-task", "main-bot"
        )
        is None
    )


def test_reopen_rejects_unconsumed_ticket() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)

    with pytest.raises(TaskStateError, match="cannot be reopened"):
        RelayCoordinator(graph_service).reopen(
            "relay-task", "main-bot", "unrelated-token"
        )


def test_begin_event_returns_processing_within_ttl_then_completes() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)
    coordinator = RelayCoordinator(graph_service)
    key = "EXECUTION_RESULT:bis"

    assert coordinator.begin_event("relay-task", key) == {"state": "CLAIMED"}
    # A concurrent replica re-entering inside the TTL does not double-claim.
    assert coordinator.begin_event("relay-task", key) == {"state": "PROCESSING"}

    coordinator.complete_event("relay-task", key, {"ok": True})
    replay = coordinator.begin_event("relay-task", key)
    assert replay["state"] == "COMPLETED"
    assert replay["result"] == {"ok": True}


def test_release_event_without_reservation_is_a_noop() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)
    before = dict(
        graph_service.query_task_dashboard("relay-task").extend_props.get(
            "relay_event_records"
        )
        or {}
    )

    RelayCoordinator(graph_service).release_event("relay-task", "never-seen")

    after = graph_service.query_task_dashboard("relay-task").extend_props.get(
        "relay_event_records"
    )
    assert after == before  # unknown reservation: nothing added, nothing removed
    assert "never-seen" not in after


def test_expire_turn_refuses_active_lease_and_foreign_holder() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)
    coordinator = RelayCoordinator(graph_service)

    assert coordinator.expire_turn("relay-task", "relay-task", "main-bot") is False
    assert coordinator.expire_turn("relay-task", "relay-task", "other-bot") is False


def test_seen_and_mark_event_round_trip() -> None:
    service, graph_service = _service()
    _grant_root_turn(service)
    coordinator = RelayCoordinator(graph_service)

    assert coordinator.seen_event("relay-task", "evt-9") is False
    coordinator.mark_event("relay-task", "evt-9")
    coordinator.mark_event("relay-task", "evt-9")  # idempotent re-mark
    assert coordinator.seen_event("relay-task", "evt-9") is True


# =====================================================================
# relay: diagnostic helpers
# =====================================================================


def test_diagnostic_text_truncates_overlong_values() -> None:
    long_text = "x" * 2500

    short = _diagnostic_text(long_text)
    assert short.startswith("x" * 2000)
    assert short.endswith("...(truncated)")
    assert _diagnostic_text(None) is None


def test_relay_attempt_defaults_to_zero_on_query_failure() -> None:
    def _boom(_task_id):
        raise RuntimeError("graph unreadable")

    hostile = SimpleNamespace(_graph=SimpleNamespace(query_task_dashboard=_boom))

    assert relay_attempt(hostile, "t-boot") == 0


def test_emit_relay_event_swallows_emitter_failures() -> None:
    class _BoomCtx:
        def emit_trajectory_event(self, *_args, **_kwargs):
            raise RuntimeError("trajectory down")

    hostile = SimpleNamespace(_task_context_service=_BoomCtx())

    emit_relay_event(  # must not raise and must surface as a WARNING only
        hostile,
        task_id="t-relay",
        node_id="t-relay",
        action_result="bootstrap",
    )


def test_emit_relay_callback_success_defaults_attempt_on_query_failure() -> None:
    class Recording:
        def __init__(self):
            self.emitted: list[dict] = []

        def _emit_relay(self, **kwargs):
            self.emitted.append(kwargs)

    def _boom(_task_id):
        raise RuntimeError("graph unreadable")

    recorder = Recording()
    hostile = SimpleNamespace(
        _graph=SimpleNamespace(query_task_dashboard=_boom),
        _emit_relay=recorder._emit_relay,
    )

    emit_relay_callback_success(
        hostile,
        task_id="t-relay",
        node_id="n1",
        event_type="EXECUTION_RESULT",
        event_id="e1",
        holder_id="main-bot",
        relay_turn="secret-tok",
        payload={"a": 1, "b": 2},
        result={"idempotent": True, "completed": False},
    )

    assert len(recorder.emitted) == 1
    event = recorder.emitted[0]
    assert event["action_result"] == "callback_reported"
    assert event["attempt"] == 0
    assert event["ext_info"]["idempotent"] is True
    assert event["ext_info"]["payload_keys"] == ["a", "b"]
    assert event["ext_info"]["relay_turn_prefix"] == "secret-t"


def test_emit_relay_callback_error_without_correlation_is_dropped() -> None:
    class Recording:
        def __init__(self):
            self.emitted: list[dict] = []

        def _emit_relay(self, **kwargs):
            self.emitted.append(kwargs)

    recorder = Recording()
    hostile = SimpleNamespace(_emit_relay=recorder._emit_relay)

    emit_relay_callback_error(
        hostile,
        task_id="",
        node_id="",
        event_type="EXECUTION_RESULT",
        event_id="e1",
        holder_id="main-bot",
        relay_turn=None,
        progress_reason=None,
        failure_reason=None,
        payload=None,
        error_phase="ingest",
        exception_type="ValueError",
        error_msg="no correlation possible",
    )

    assert recorder.emitted == []  # dropped without correlation, no raise


def test_emit_relay_callback_error_defaults_attempt_on_query_failure() -> None:
    class Recording:
        def __init__(self):
            self.emitted: list[dict] = []

        def _emit_relay(self, **kwargs):
            self.emitted.append(kwargs)

    def _boom(_task_id):
        raise RuntimeError("graph unreadable")

    recorder = Recording()
    hostile = SimpleNamespace(
        _graph=SimpleNamespace(query_task_dashboard=_boom),
        _emit_relay=recorder._emit_relay,
    )

    emit_relay_callback_error(
        hostile,
        task_id="t-relay",
        node_id="n1",
        event_type="PLAN_RESULT",
        event_id="e2",
        holder_id="main-bot",
        relay_turn="secret-tok",
        progress_reason="plan",
        failure_reason=None,
        payload={"gaps": ["g"]},
        error_phase="processing",
        exception_type="TaskStateError",
        error_msg="bad plan",
    )

    assert len(recorder.emitted) == 1
    event = recorder.emitted[0]
    assert event["attempt"] == 0
    assert event["ext_info"]["error_phase"] == "processing"
    assert event["ext_info"]["relay_turn_prefix"] == "secret-t"


def test_emit_relay_callback_error_survives_emitter_failure() -> None:
    class _BoomRecorder:
        def _emit_relay(self, **_kwargs):
            raise RuntimeError("emitter exploded")

    hostile = SimpleNamespace(_emit_relay=_BoomRecorder()._emit_relay)

    emit_relay_callback_error(  # fire-and-forget: diagnostics never mask the error
        hostile,
        task_id="t-relay",
        node_id="n1",
        event_type="PLAN_RESULT",
        event_id="e3",
        holder_id="main-bot",
        relay_turn=None,
        progress_reason=None,
        failure_reason="gap",
        payload=None,
        error_phase="persist",
        exception_type="RuntimeError",
        error_msg="boom",
    )


# =====================================================================
# task_service: facade edges
# =====================================================================


def test_discover_property_exposes_injected_discover_port() -> None:
    discover = _DiscoverTwo()
    service, _ = _build_service(discover=discover)

    assert service._discover is discover  # compat accessor reaches the adapter port


def test_materialize_static_plan_reraises_task_state_errors_unchanged() -> None:
    class _HostileBindings:
        @property
        def bot_id_by_role(self):
            raise TaskStateError("corp bindings unavailable")

    service, _ = _build_service(bot_bindings=_HostileBindings())
    okr_request = replace(
        _request(),
        execution_config={"task_type": "dynamic"},
        task_spec=RequestTaskSpec(
            context=RequestContext(title="okr 双十一冲刺"),
            goal=RequestGoal(objective="okr 转化率提升", acceptances=[]),
        ),
    )

    with pytest.raises(TaskStateError, match="corp bindings unavailable"):
        service._materialize_static_plan_if_needed(okr_request)


def test_materialize_static_plan_wraps_unexpected_errors() -> None:
    class _HostileBindings:
        @property
        def bot_id_by_role(self):
            raise RuntimeError("kaboom")

    service, _ = _build_service(bot_bindings=_HostileBindings())
    okr_request = replace(
        _request(),
        execution_config={"task_type": "dynamic"},
        task_spec=RequestTaskSpec(
            context=RequestContext(title="okr 大促"),
            goal=RequestGoal(objective="okr 转化率", acceptances=[]),
        ),
    )

    with pytest.raises(TaskStateError, match="static plan template validation failed"):
        service._materialize_static_plan_if_needed(okr_request)


def test_converge_by_session_returns_false_without_repo_or_session() -> None:
    service, _ = _service()

    assert _run(service.converge_by_session("sid-no-repo", success=True)) is False
    assert _run(service.converge_by_session("", success=True)) is False


def test_converge_by_session_warns_and_returns_false_when_report_fails() -> None:
    class _RunRepo:
        def get_by_session_id(self, session_id):
            record = TaskNodeRunInfoRecord(
                id=0,
                node_id="relay-task",
                task_id="relay-task",
                run_mode="single_bot",
                assignee="b1",
                output=None,
                acceptance_result=None,
                retry=0,
                session_id=session_id,
                extend_props=None,
                start_time=None,
                update_time=None,
                end_time=None,
            )
            return record

    service, _ = _build_service(task_node_run_info_repo=_RunRepo())

    async def _boom(_data):
        raise RuntimeError("on_report exploded")

    original = service._callback.report_result
    service._callback.report_result = _boom
    try:
        assert _run(service.converge_by_session("sid-1", success=True)) is False
    finally:
        del service._callback.report_result
        del original


class _FakeCallbackRepo:
    def __init__(self, *, fail_upsert: bool = False):
        self.rows: list = []
        self.fail_upsert = fail_upsert

    def get_latest_by_session(self, session_id):
        for row in reversed(self.rows):
            if row.run_id == session_id:
                return row
        return None

    def upsert(self, rec):
        if self.fail_upsert:
            raise RuntimeError("db down")
        self.rows.append(rec)


def _mw_completed_event() -> dict:
    return {
        "event_id": "evt-mw-1",
        "event_type": "session.completed",
        "scope": {"session_id": "sess-mw", "group_id": "grp-mw"},
        "data": {"reason": "completed", "summary": "协作进度汇总"},
    }


def test_apply_manager_worker_event_ignores_foreign_payloads() -> None:
    callback_repo = _FakeCallbackRepo()
    service, _ = _build_service(callback_repo=callback_repo)

    _run(service.apply_manager_worker_event({"event_type": "task.vanished"}))
    _run(service.apply_manager_worker_event("not-a-dict"))

    assert callback_repo.rows == []


def test_apply_manager_worker_event_upserts_merged_graph_and_converges() -> None:
    callback_repo = _FakeCallbackRepo()
    service, _ = _build_service(callback_repo=callback_repo)

    _run(service.apply_manager_worker_event(_mw_completed_event()))

    assert len(callback_repo.rows) == 1
    row = callback_repo.rows[0]
    assert row.run_id == "sess-mw"
    assert row.status == Status.DONE  # session.completed maps to DONE
    assert row.execution_graph["session_summary"] == "协作进度汇总"
    assert row.execution_graph["session_id"] == "sess-mw"
    assert row.orig_callback_data  # raw envelope retained for audit


def test_apply_manager_worker_event_survives_upsert_failure() -> None:
    callback_repo = _FakeCallbackRepo(fail_upsert=True)
    service, _ = _build_service(callback_repo=callback_repo)

    # The session.completed converge attempt still runs after the store fails.
    _run(service.apply_manager_worker_event(_mw_completed_event()))

    assert callback_repo.rows == []


def test_apply_manager_worker_event_warns_when_converge_raises() -> None:
    class _BoomRunRepo:
        def get_by_session_id(self, session_id):
            raise RuntimeError("lookup exploded")

    callback_repo = _FakeCallbackRepo()
    service, _ = _build_service(
        callback_repo=callback_repo, task_node_run_info_repo=_BoomRunRepo()
    )

    # converge raises inside apply; the failure is logged, never propagated.
    _run(service.apply_manager_worker_event(_mw_completed_event()))

    assert len(callback_repo.rows) == 1  # storage happened before the failure


def test_on_bg_done_ignores_cancelled_and_logs_failed_tasks() -> None:
    async def scenario() -> None:
        service, _ = _service()

        async def boom():
            raise RuntimeError("background died")

        failed = asyncio.create_task(boom())
        await asyncio.sleep(0)
        service._on_bg_done(failed)  # logs the exception (682)

        cancelled = asyncio.create_task(asyncio.sleep(0))
        cancelled.cancel()
        try:
            await cancelled
        except asyncio.CancelledError:
            pass
        service._on_bg_done(cancelled)  # cancelled tasks stay silent (679)

    _run(scenario())  # no exception escapes either branch


def test_redrive_task_schedules_background_redrive() -> None:
    async def scenario() -> None:
        service, _ = _service()
        await service.execute(_request())

        await service.redrive_task("relay-task")

        assert service._bg_tasks  # the redrive is scheduled in the tracked set
        await service.drain_background()
        assert not service._bg_tasks

    _run(scenario())


def test_enrich_anniversary_trigger_bot_name_resolves_display_name() -> None:
    service, _ = _build_service(bot_service=_BotSvc())
    request = replace(
        _request(),
        execution_config={
            "task_type": "dynamic",
            "static_plan_id": "merchant-operations-goal-to-plan",
        },
        owner_bot_id="trigger-bot:owner-1",
    )
    graph = SimpleNamespace(task_id="relay-task", extend_props={})

    service._enrich_anniversary_trigger_bot_name(request, graph)

    assert graph.extend_props["trigger_bot_name"] == "OK Bot"


def test_enrich_anniversary_trigger_bot_name_degrades_without_owner_bot() -> None:
    service, _ = _build_service(bot_service=_BotSvc(fail_by_id=True))
    request = replace(
        _request(),
        execution_config={
            "task_type": "dynamic",
            "static_plan_id": "merchant-operations-goal-to-plan",
        },
        owner_bot_id="",
    )
    graph = SimpleNamespace(task_id="relay-task", extend_props={})

    service._enrich_anniversary_trigger_bot_name(request, graph)
    assert "trigger_bot_name" not in graph.extend_props

    request_with_bots = replace(request, owner_bot_id="trigger-bot:owner-1")
    service._enrich_anniversary_trigger_bot_name(request_with_bots, graph)
    # lookup raised → display stays unset, execute is never blocked
    assert "trigger_bot_name" not in graph.extend_props


def test_update_task_node_info_folds_non_state_fields() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))

    result = _run(
        service.update_task_node_info(
            "relay-task",
            "relay-task",
            assignee="main-bot",
            output_patch={"folded": "value"},
            extend_props_patch={"extra": "1"},
            progress_reason="内部写口 fold",
        )
    )

    assert result.success is True
    root = graph_service.query_task_dashboard("relay-task").tasks[0]
    assert root.run_info.output == {"folded": "value"}
    assert root.run_info.extend_props["extra"] == "1"
    assert root.status == Status.RUNNING  # fold-only writes never flip status


def test_run_execute_drives_the_facade_synchronously() -> None:
    service, _ = _service()

    result = run_execute(service, _request())

    assert result.success is True
    assert result.task_id == "relay-task"
    assert result.extend_props["orchestration_mode"] == "relay"


# =====================================================================
# task_service_execution: workflow/yaml failure modes + bbs delegation
# =====================================================================


def test_run_workflow_returns_error_when_runner_start_raises() -> None:
    class _BoomRunner:
        async def start_run(self, _nodes):
            raise RuntimeError("runner exploded")

    class _BoomEngine:
        def __init__(self):
            self._runner = _BoomRunner()

    service, graph_service = _build_service(
        relay_settings=False, task_id_provider=lambda: "t-wf"
    )
    service._centralized_adapter = _BoomEngine()

    result = _run(service.execute(_typed_request("workflow", workflow_id="wf")))

    assert result.success is False
    assert "workflow trigger failed" in result.error
    graph = graph_service.query_task_dashboard("t-wf")
    assert graph.tasks[0].status == Status.RUNNING


def test_run_yaml_returns_error_when_group_start_fails() -> None:
    class _BoomEngine:
        async def start_coop_group(self, _formation):
            raise RuntimeError("bcs down")

    service, graph_service = _build_service(
        relay_settings=False, task_id_provider=lambda: "t-yaml"
    )
    service._centralized_adapter = _BoomEngine()

    result = _run(
        service.execute(
            _typed_request(
                "yaml", yaml="kind: collab", participant_bot_ids=["default:b2"]
            )
        )
    )

    assert result.success is False
    assert "yaml group failed" in result.error
    assert graph_service.query_task_dashboard("t-yaml").status == Status.RUNNING


def test_run_bbs_delegates_hung_and_escalation_and_returns_success() -> None:
    class _RecordingEngine:
        def __init__(self):
            self.calls: list[tuple] = []

        def _hung_and_escalate(self, *, task_id, node_id, hung_reason):
            self.calls.append((task_id, node_id, hung_reason))

    engine = _RecordingEngine()
    service, graph_service = _build_service(
        relay_settings=False, task_id_provider=lambda: "t-bbs"
    )
    service._centralized_adapter = engine

    result = _run(service.execute(_typed_request("bbs")))

    assert result.success is True
    assert result.extend_props == {}
    assert engine.calls == [("t-bbs", "t-bbs", "创建BBS接力任务")]
    assert graph_service.query_task_dashboard("t-bbs").tasks[0].node_id == "t-bbs"


def test_persist_node_run_skips_legacy_repos_when_graph_repository_bound() -> None:
    class _GraphWithRepo(TaskGraphService):
        @property
        def has_repository(self):
            return True

    class _RecordingNodeRepo:
        def __init__(self):
            self.inserts: list = []

        def insert(self, rec):
            self.inserts.append(rec)

    class _RecordingRunRepo(_RecordingNodeRepo):
        pass

    node_repo, run_repo = _RecordingNodeRepo(), _RecordingRunRepo()
    service, _ = _build_service(
        graph=_GraphWithRepo(),
        task_node_repo=node_repo,
        task_node_run_info_repo=run_repo,
        task_id_provider=lambda: "t-persist",
    )
    request = _request()
    info = request.to_task_info("t-persist")

    service._persist_node_run(
        "t-persist",
        info,
        run_mode="coop_group",
        assignee="grp-1",
        session_id="sess-1",
        extend_props={"group_id": "grp-1"},
    )
    # Shared persistence path owns the write; the legacy repos stay untouched.
    assert node_repo.inserts == []
    assert run_repo.inserts == []

    plain_service, _ = _build_service(
        task_node_repo=node_repo, task_node_run_info_repo=run_repo
    )
    plain_service._persist_node_run(
        "relay-task",
        info,
        run_mode="coop_group",
        assignee="grp-1",
        session_id="sess-1",
        extend_props=None,
    )
    # Without the aggregate repository both legacy sinks are written.
    assert len(node_repo.inserts) == 1
    assert len(run_repo.inserts) == 1
    assert run_repo.inserts[0].session_id == "sess-1"


# =====================================================================
# task_service_queries: dashboard hydration + display enrichment
# =====================================================================


def test_hydrate_treats_non_dict_execution_config_as_empty() -> None:
    graph_service = TaskGraphService()
    service, _ = _build_service(
        graph=graph_service, task_id_provider=lambda: "t-stringy"
    )
    graph_service.initialize_graph(_info("t-stringy"))
    graph_service.update_task_graph_info(
        "t-stringy",
        TaskGraphPatch(extend_props_patch={"execution_config": "not-a-dict"}),
    )

    dashboard = service.get_task_dashboard("t-stringy")

    root = next(n for n in dashboard.tasks if n.node_id == "t-stringy")
    # A malformed config degrades to empty: identity is still backfilled from
    # the graph-level owner/source metadata instead of crashing the dashboard.
    assert root.run_info.run_mode == "single_bot"
    assert root.status == Status.PENDING


def test_hydrate_backfills_session_id_into_missing_root_runtime() -> None:
    graph_service = TaskGraphService()
    service, _ = _build_service(
        graph=graph_service, task_id_provider=lambda: "t-session"
    )
    graph_service.initialize_graph(_info("t-session"))
    config = dict(_info("t-session").execution_config)
    config["main_session_id"] = "sess-added-later"
    # The graph was created before the session bound; the dashboard backfills it.
    graph_service.update_task_graph_info(
        "t-session",
        TaskGraphPatch(extend_props_patch={"execution_config": config}),
    )

    dashboard = service.get_task_dashboard("t-session")

    root = next(n for n in dashboard.tasks if n.node_id == "t-session")
    assert root.run_info.extend_props["session_id"] == "sess-added-later"


def test_dashboard_survives_callback_repo_lookup_failure() -> None:
    class _ExplodingCallbackRepo:
        def get_latest_by_session(self, _session_id):
            raise RuntimeError("callback store down")

    graph_service = TaskGraphService()
    service, _ = _build_service(
        graph=graph_service,
        callback_repo=_ExplodingCallbackRepo(),
        task_id_provider=lambda: "t-cbl",
    )
    graph_service.initialize_graph(_info("t-cbl"))
    graph_service.update_task_node_info(
        TaskNodePatch(
            task_id="t-cbl",
            node_id="t-cbl",
            extend_props_patch={"session_id": "sess-boom"},
        )
    )

    dashboard = service.get_task_dashboard("t-cbl")

    # The read is never blocked by the diagnostic session lookup.
    assert dashboard.execution_graph is None
    assert dashboard.tasks[0].run_info.extend_props["session_id"] == "sess-boom"


def _display_graph(task_id: str = "t-attach") -> TaskExecutionGraph:
    def node(node_id, run_mode, assignee, extend=None):
        return TaskNode(
            node_id=node_id,
            task_id=task_id,
            status=Status.RUNNING,
            task_spec=TaskSpec(
                context=Context(background="bg", title=node_id),
                goal=Goal(objective="o", acceptances=[]),
            ),
            run_info=RuntimeInfo(
                run_mode=run_mode, assignee=assignee, extend_props=dict(extend or {})
            ),
            node_run_graph=None,  # type: ignore[arg-type]
        )

    graph = TaskExecutionGraph(run_id=1, loop_round=0, status=Status.RUNNING)
    graph.task_id = task_id
    graph.tasks = [
        node("n-empty", "single_bot", ""),  # bare assignee: nothing to enrich
        node("n-pair-hostile", "single_bot", "b-pair:u2", {"assignee_owner_id": "u2"}),
        node("n-id-hostile", "single_bot", "b-solo"),  # legacy row w/o owner
        node("n-ok", "single_bot", "b-ok:u1", {"assignee_owner_id": "u1"}),
        node("n-bbs", "bbs", "b-bbs:u9", {"assignee_owner_id": "u9"}),
        node("n-coop", "coop_group", "grp-x"),  # non single/bbs rows stay untouched
    ]
    return graph


class _PerPairBotSvc:
    """Hostile per-pair: ok for normal pairs, raising for marked ones."""

    def __init__(self, hostile_pairs, hostile_ids):
        self.hostile_pairs = set(hostile_pairs)
        self.hostile_ids = set(hostile_ids)

    def list_bots_by_owner_bot_pairs(self, *, pairs, page=1, page_size=20):
        del page, page_size
        if any(pair in self.hostile_pairs for pair in pairs):
            raise RuntimeError("pair catalog down")
        return {
            "items": [
                {"bot_id": bot, "owner_id": owner, "bot_name": f"{bot}-name"}
                for bot, owner in pairs
            ]
        }

    def get_bot_by_id(self, bot_id):
        if bot_id in self.hostile_ids:
            raise RuntimeError("bot store down")
        return {"bot_id": bot_id, "owner_id": "u1", "bot_name": f"{bot}-name"}


def test_attach_assignee_bot_info_enriches_and_degrades_per_node() -> None:
    service, _ = _build_service(
        bot_service=_PerPairBotSvc(
            hostile_pairs=[("b-pair", "u2")], hostile_ids={"b-solo"}
        )
    )
    graph = _display_graph()

    service._attach_assignee_bot_info(graph)

    by_id = {n.node_id: n for n in graph.tasks}
    assert by_id["n-ok"].run_info.extend_props["assignee_name"] == "b-ok-name"
    assert by_id["n-ok"].run_info.extend_props["assignee_owner_id"] == "u1"
    assert by_id["n-bbs"].run_info.extend_props["assignee_name"] == "b-bbs-name"
    assert "assignee_name" not in by_id["n-coop"].run_info.extend_props
    # empty assignee → skipped; failing lookups degrade to "no info" silently
    assert "assignee_name" not in by_id["n-empty"].run_info.extend_props
    assert "assignee_name" not in by_id["n-pair-hostile"].run_info.extend_props
    assert "assignee_name" not in by_id["n-id-hostile"].run_info.extend_props


def _info_record(owner_bot_id: str, owner_user_id: str) -> TaskInfoRecord:
    return TaskInfoRecord(
        id=0,
        task_id="t-list",
        source_type="bot",
        owner_user_id=owner_user_id,
        owner_bot_id=owner_bot_id,
        execution_config={},
        task_spec={},
        status=Status.PENDING,
    )


def test_enrich_task_owner_display_resolves_bot_and_user_names() -> None:
    service, _ = _build_service(bot_service=_BotSvc(), staff_dept=_StaffDept())

    enriched = service._enrich_task_owner_display(
        [
            _info_record("b1", "u1"),
            _info_record("b2:u2", "u2"),
        ]
    )

    assert enriched[0].owner_bot_id == "b1"
    assert enriched[0].owner_bot_name == "Name1"
    assert enriched[0].owner_user_name == "User-u1"
    assert enriched[1].owner_bot_id == "b2"  # composite storage normalized
    assert enriched[1].owner_user_id == "u2"


def test_enrich_task_owner_display_degrades_on_lookup_failures() -> None:
    service, _ = _build_service(
        bot_service=_BotSvc(fail_pairs=True), staff_dept=_StaffDept(raise_profile=True)
    )

    enriched = service._enrich_task_owner_display(
        [_info_record("b1", "u1"), _info_record("b-anon", "")],
    )

    assert enriched[0].owner_bot_name is None
    assert enriched[0].owner_user_name is None
    assert enriched[1].owner_user_name is None  # missing owner skipped for staff
    # enrichment never rewrites owner identity
    assert enriched[1].owner_bot_id == "b-anon"


def test_list_apis_return_empty_without_task_info_repository() -> None:
    service, _ = _service()

    assert service.list_tasks() == []
    assert service.list_tasks(status="pending") == []
    assert service.list_tasks_page() == ([], 0)


def _bbs_overview_record() -> BbsTaskOverviewRecord:
    return BbsTaskOverviewRecord(
        task_id="t-bbs",
        node_id="n-bbs",
        run_mode="bbs",
        retry=0,
        assignee_id=None,
        status=Status.PENDING,
        acceptance_result=None,
        extend_props=None,
        relay_create_time=None,
        relay_begin_time=None,
        relay_end_time=None,
        task_spec={},
        publisher="b1",
        owner_user_id="u1",
    )


def test_enrich_bbs_publisher_names_ignores_malformed_items() -> None:
    service, _ = _build_service(bot_service=_BotSvc())

    enriched = service._enrich_bbs_publisher_names([_bbs_overview_record()])

    assert enriched[0].publisher_name == "Name1"
    assert service._enrich_bbs_publisher_names([]) == []


def test_enrich_bbs_publisher_names_degrades_when_bot_catalog_fails() -> None:
    service, _ = _build_service(bot_service=_BotSvc(fail_pairs=True))

    enriched = service._enrich_bbs_publisher_names([_bbs_overview_record()])

    assert enriched[0].publisher_name is None  # display-only: lookup failure degrades


def test_list_bbs_tasks_facade_delegates_pagination_and_filtering() -> None:
    service, _ = _service()

    records, total = service.list_bbs_tasks(1, 10, search_word="关键词", status="pending")

    # No graph repository bound in the in-memory path: an empty page, not an error.
    assert records == []
    assert total == 0


# =====================================================================
# task_service_relay + dispatch: relay event guards
# =====================================================================


def test_report_fact_with_event_id_persists_durable_outbox_audit() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))

    service._report_fact(
        "RELAY_TURN_MARK_EVENT",
        {
            "task_id": "relay-task",
            "node_id": "relay-task",
            "holder_id": "main-bot",
            "event_key": "EXECUTION_RESULT:audit",
        },
        event_id="custom-audit-42",
    )

    outbox = graph_service.query_task_dashboard("relay-task").extend_props[
        "_task_outbox"
    ]
    assert any(str(item["event_id"]) == "custom-audit-42" for item in outbox)


def test_report_add_nodes_appends_relay_successor_node() -> None:
    service, graph_service = _service()
    turn = _grant_root_turn(service)
    _plan_next(
        service,
        turn,
        "plan-add",
        {"gaps": ["补齐市场研究 gap"], "next_task_spec": _child_spec()},
    )

    extra = TaskNode(
        node_id="relay-extra",
        task_id="relay-task",
        status=Status.PENDING,
        task_spec=TaskSpec(
            context=Context(background="bg", title="补充执行"),
            goal=Goal(objective="补一棒", acceptances=[]),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )
    service._report_add_nodes(
        "relay-task", [extra], parent_node_id="relay-task", mark_parent_planning=False
    )

    graph = graph_service.query_task_dashboard("relay-task")
    assert "relay-extra" in [n.node_id for n in graph.tasks]
    assert ("relay-task", "relay-extra") in [
        (r.src_id, r.dst_id) for r in graph.relations
    ]
    # mark_parent_planning=False keeps the handed-off root DONE
    root = next(n for n in graph.tasks if n.node_id == "relay-task")
    assert root.status == Status.DONE


def test_relay_node_rejects_non_relay_mode_and_missing_node() -> None:
    relay_service, _ = _service()
    _run(relay_service.execute(_request()))

    plain_service, _ = _build_service(
        relay_settings=False, task_id_provider=lambda: "t-plain2"
    )
    _run(plain_service.execute(_typed_request("workflow", workflow_id="wf")))
    with pytest.raises(TaskStateError, match="not in relay mode"):
        plain_service._relay_node("t-plain2", "t-plain2")

    with pytest.raises(TaskStateError, match="relay node not found"):
        relay_service._relay_node("relay-task", "ghost-node")


def test_search_result_alias_dispatches_like_dispatch_result() -> None:
    service, graph_service = _service()
    turn = _grant_root_turn(service)
    planned = _plan_next(
        service,
        turn,
        "plan-alias",
        {"gaps": ["补齐市场研究 gap"], "next_task_spec": _child_spec()},
    )
    target = planned["target_node_id"]

    result = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=target,
            event_type="SEARCH_RESULT",
            event_id="search-alias",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="候选 Bot 能力与下一节点目标匹配",
            payload={
                "outcome": "HIT_SINGLE",
                "driver_bot_id": "alias-bot",
                "next_relay_bots": ["alias-bot"],
            },
        )
    )

    assert result["driver_bot_id"] == "alias-bot"
    node = next(n for n in graph_service.query_task_dashboard("relay-task").tasks if n.node_id == target)
    assert node.run_info.assignee == "alias-bot"


def test_report_task_event_rejects_unknown_event_type() -> None:
    service, _ = _service()
    _run(service.execute(_request()))

    with pytest.raises(TaskStateError, match="unsupported relay event_type"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="MYSTERY_EVENT",
                event_id="mystery",
                holder_id="main-bot",
                progress_reason="未知事件类型",
                payload={},
            )
        )


def test_executing_handed_off_running_node_rejects_new_execution_report() -> None:
    class _PhantomRelayGraph(TaskGraphService):
        def query_task_dashboard(self, task_id, node_id=None):
            graph = super().query_task_dashboard(task_id, node_id)
            if node_id is None:
                graph.relations.append(
                    Relation(src_id="relay-task", dst_id="phantom-successor")
                )
            return graph

    service, _ = _build_service(graph=_PhantomRelayGraph())
    _run(service.execute(_request()))

    # The status guard inside report_task_event must not let a still-RUNNING
    # node with an already handed-off successor accept a fresh execution report.
    with pytest.raises(TaskStateError, match="already handed off a successor"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="phantom-exec",
                holder_id="main-bot",
                progress_reason="迟到执行事件",
                payload=_accepted("late output"),
            )
        )
    # The reservation is released: the identical retry fails identically.
    with pytest.raises(TaskStateError, match="already handed off a successor"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="phantom-exec",
                holder_id="main-bot",
                progress_reason="迟到执行事件重试",
                payload=_accepted("late output"),
            )
        )


def test_completed_execution_retry_with_foreign_holder_marks_turn_consumed() -> None:
    service, _ = _service()
    _grant_root_turn(service, event_id="exec-owner")

    reported = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec-owner",  # completed replay …
            holder_id="not-the-holder",  # … by a foreign holder
            progress_reason="重试执行事件",
            payload=_accepted("done"),
        )
    )

    # The completed event stays idempotent, but a fresh turn cannot be granted
    # to a foreign holder: the graph reports the turn as already consumed.
    assert reported["ok"] is True
    assert reported["idempotent"] is True
    assert reported["turn_consumed"] is True


def test_event_in_progress_reservation_returns_idempotent_in_flight() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    # A crashed replica left a PROCESSING reservation behind: the retry must be
    # an idempotent in-flight reply rather than a second claim.
    graph_service.update_task_graph_info(
        "relay-task",
        TaskGraphPatch(
            extend_props_patch={
                "relay_event_records": {
                    "EXECUTION_RESULT:evtproc": {
                        "state": "PROCESSING",
                        "claimed_at_ms": int(time.time() * 1000),
                    }
                }
            }
        ),
    )

    result = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="evtproc",
            holder_id="main-bot",
            progress_reason="处理中的事件重试",
            payload=_accepted("done"),
        )
    )

    assert result == {"ok": True, "idempotent": True, "event_in_progress": True}


def test_new_execution_requires_a_granted_turn_invariant() -> None:
    service, _ = _service()
    _run(service.execute(_request()))
    service._report_relay_turn = lambda report_type, **payload: None  # hostile seam

    with pytest.raises(AssertionError, match="did not receive a turn"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec-no-turn",
                holder_id="main-bot",
                progress_reason="权证授予失败的执行上报",
                payload=_accepted("done"),
            )
        )


def test_plan_result_requires_relay_turn_after_execution_report() -> None:
    service, _ = _service()
    _grant_root_turn(service)

    with pytest.raises(TaskStateError, match="relay_turn is required"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="PLAN_RESULT",
                event_id="plan-no-turn",
                holder_id="main-bot",
                progress_reason="规划继续",
                payload={"gaps": [], "next_task_spec": None},
            )
        )


def _two_baton_state(service):
    """Root exec → plan → select → dispatch → second bot exec. Returns (target, turn2)."""
    turn = _grant_root_turn(service)
    target = _plan_and_select(
        service,
        origin_node_id="relay-task",
        holder_id="main-bot",
        turn=turn,
        child_node_id="ignored",
        event_suffix="1",
    )
    _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id="relay-task",
            target_node_id=target,
            holder_id="main-bot",
            relay_turn=turn,
            dispatch_id="dispatch-two",
        )
    )
    second = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=target,
            event_type="EXECUTION_RESULT",
            event_id="exec-two",
            holder_id="research-bot",
            progress_reason="第二棒完成",
            payload=_accepted({"secondary": "done"}),
        )
    )
    return target, second["relay_turn"]


def test_plan_must_target_the_turn_origin_node() -> None:
    service, _ = _service()
    _target, turn2 = _two_baton_state(service)

    with pytest.raises(TaskStateError, match="target the turn origin node"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",  # stale node while the second bot holds the baton
                event_type="PLAN_RESULT",
                event_id="plan-wrong-origin",
                holder_id="research-bot",
                relay_turn=turn2,
                progress_reason="越权续棒",
                payload={"gaps": [], "next_task_spec": None},
            )
        )


def test_dispatch_decision_must_stay_inside_current_turn_plan() -> None:
    service, _ = _service()
    target, turn2 = _two_baton_state(service)

    with pytest.raises(TaskStateError, match="outside the current turn plan"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=target,
                event_type="DISPATCH_RESULT",
                event_id="search-wrong-plan",
                holder_id="research-bot",
                relay_turn=turn2,
                progress_reason="越权派发决策",
                payload={"outcome": "HIT_SINGLE", "driver_bot_id": "x-bot"},
            )
        )


def test_declined_execution_defaults_from_success_flag() -> None:
    service, _ = _service()
    _run(service.execute(_request()))
    # No explicit decision + success=False must resolve to DECLINED with reason.
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec-decline-default",
            holder_id="main-bot",
            progress_reason="职责不覆盖",
            failure_reason="capability_mismatch",
            payload={"success": False},
        )
    )

    assert service.get_task_context("relay-task").all_done_output == []


def test_execution_decision_must_be_accept_or_decline() -> None:
    service, _ = _service()
    _run(service.execute(_request()))

    with pytest.raises(TaskStateError, match="ACCEPTED or DECLINED"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec-maybe",
                holder_id="main-bot",
                progress_reason="含糊决定",
                payload={"execution_decision": "MAYBE", "output": {}},
            )
        )


def test_accepted_execution_requires_acceptance_result() -> None:
    service, _ = _service()
    _run(service.execute(_request()))

    with pytest.raises(TaskStateError, match="requires acceptance_result"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec-no-acceptance",
                holder_id="main-bot",
                progress_reason="缺验收的接受",
                payload={"output": {}},
            )
        )


def test_declined_execution_rejects_business_facts() -> None:
    service, _ = _service()
    _run(service.execute(_request()))

    with pytest.raises(TaskStateError, match="cannot contain business facts"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec-leaky-decline",
                holder_id="main-bot",
                progress_reason="拒绝但夹带产出",
                payload={
                    "execution_decision": "DECLINED",
                    "output": {"leak": "value"},
                },
            )
        )


def test_goal_from_payload_builds_goal_only_for_dicts_with_objective() -> None:
    assert TaskService._goal_from_payload("not-a-dict") is None
    assert TaskService._goal_from_payload({"objective": "  "}) is None

    goal = TaskService._goal_from_payload(
        {
            "objective": "重新聚焦细分市场",
            "acceptances": [
                {"id": "a1", "description": "结论可执行"},
                "not-a-dict",
            ],
        }
    )
    assert goal is not None
    assert goal.objective == "重新聚焦细分市场"
    assert [item.id for item in goal.acceptances] == ["a1"]


def test_accepted_execution_surfaces_overridden_actual_goal() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))

    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec-goal-override",
            holder_id="main-bot",
            progress_reason="实际执行目标覆盖声明目标",
            payload={
                "execution_decision": "ACCEPTED",
                "actual_goal": {
                    "objective": "重新聚焦细分市场",
                    "acceptances": [],
                },
                "output": {"summary": "done"},
                "acceptance_result": {"verdict": "DONE", "done_items": [], "gap_items": []},
            },
        )
    )

    root = graph_service.query_task_dashboard("relay-task").tasks[0]
    assert root.run_info.actual_goal is not None
    assert root.run_info.actual_goal.objective == "重新聚焦细分市场"


def test_invalid_acceptance_verdict_is_rejected() -> None:
    service, _ = _service()
    _run(service.execute(_request()))

    with pytest.raises(TaskStateError, match="invalid acceptance_result.verdict"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec-bad-verdict",
                holder_id="main-bot",
                progress_reason="非法验收结果",
                payload={
                    "execution_decision": "ACCEPTED",
                    "output": {},
                    "acceptance_result": {"verdict": "NONSENSE stuk"},
                },
            )
        )


def test_resume_expired_relay_turn_rejects_non_relay_graph() -> None:
    graph_service = TaskGraphService()
    service, _ = _build_service(
        graph=graph_service, relay_settings=False, task_id_provider=lambda: "t-resume"
    )
    graph_service.initialize_graph(_info("t-resume"))

    assert _run(service.resume_expired_relay_turn("t-resume")) is False


def _patch_relay_turn(graph_service, relay_turn: dict) -> None:
    graph_service.update_task_graph_info(
        "relay-task", TaskGraphPatch(extend_props_patch={"relay_turn": relay_turn})
    )


def test_resume_expired_relay_turn_requires_node_and_holder_identity() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))

    _patch_relay_turn(graph_service, {"status": "GRANTED", "expires_at_ms": 0})
    assert _run(service.resume_expired_relay_turn("relay-task")) is False

    _patch_relay_turn(
        graph_service,
        {
            "node_id": "node-missing-in-graph",
            "holder_id": "h",
            "status": "GRANTED",
            "expires_at_ms": 0,
        },
    )
    assert _run(service.resume_expired_relay_turn("relay-task")) is False


def test_resume_expired_relay_turn_rejects_still_active_turn() -> None:
    service, _ = _service()
    _grant_root_turn(service)  # fresh, unexpired GRANTED turn

    assert _run(service.resume_expired_relay_turn("relay-task")) is False


def test_legacy_gap_children_payload_plans_next_baton() -> None:
    service, graph_service = _service()
    turn = _grant_root_turn(service)

    planned = _plan_next(
        service,
        turn,
        "plan-legacy",
        {
            "has_gap": True,
            "children": [{"task_spec": _child_spec()}],
            "gap_detail": "市场研究缺口",
        },
    )

    assert planned["target_node_id"]
    graph = graph_service.query_task_dashboard("relay-task")
    assert any(n.node_id == planned["target_node_id"] for n in graph.tasks)


def test_next_task_spec_requires_objective() -> None:
    service, _ = _service()
    turn = _grant_root_turn(service)

    with pytest.raises(TaskStateError, match="requires goal.objective"):
        _plan_next(
            service,
            turn,
            "plan-empty-goal",
            {
                "gaps": ["补齐缺口"],
                "next_task_spec": {
                    "metadata": {"title": "空目标"},
                    "context": {},
                    "goal": {"objective": ""},
                },
            },
        )


def test_complete_relay_task_closes_node_and_graph() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))

    service._complete_relay_task("relay-task", "relay-task", "gap 已闭合，任务完成")

    graph = graph_service.query_task_dashboard("relay-task")
    root = next(n for n in graph.tasks if n.node_id == "relay-task")
    assert root.status == Status.SUCCESS
    assert graph.status == Status.DONE


def _prepare_hit_target(service):
    """Grant, plan and apply a HIT_SINGLE decision; returns (target, turn)."""
    turn = _grant_root_turn(service)
    target = _plan_and_select(
        service,
        origin_node_id="relay-task",
        holder_id="main-bot",
        turn=turn,
        child_node_id="ignored",
        event_suffix="hit",
    )
    return target, turn


def test_search_result_rejects_non_pending_target() -> None:
    service, graph_service = _service()
    target, turn = _prepare_hit_target(service)
    graph_service.update_task_node_info(
        TaskNodePatch(task_id="relay-task", node_id=target, status=Status.RUNNING)
    )

    with pytest.raises(TaskStateError, match="target must be PENDING"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=target,
                event_type="DISPATCH_RESULT",
                event_id="search-running",
                holder_id="main-bot",
                relay_turn=turn,
                progress_reason="对已运行节点重复派发",
                payload={
                    "outcome": "HIT_SINGLE",
                    "driver_bot_id": "research-bot",
                    "next_relay_bots": ["research-bot"],
                },
            )
        )


def test_hit_single_accepts_driver_only_payload() -> None:
    service, graph_service = _service()
    target, turn = _prepare_hit_target(service)

    result = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=target,
            event_type="DISPATCH_RESULT",
            event_id="search-driver-only",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="单候选即 driver",
            payload={"outcome": "HIT_SINGLE", "driver_bot_id": "solo-bot"},
        )
    )

    assert result["next_relay_bots"] == ["solo-bot"]
    node = next(
        n for n in graph_service.query_task_dashboard("relay-task").tasks if n.node_id == target
    )
    assert node.run_info.assignee == "solo-bot"
    assert node.run_info.extend_props["next_relay_bots"] == ["solo-bot"]


def test_hit_single_requires_matching_driver() -> None:
    service, _ = _service()
    target, turn = _prepare_hit_target(service)

    with pytest.raises(TaskStateError, match="HIT_SINGLE requires one next_relay_bot"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=target,
                event_type="DISPATCH_RESULT",
                event_id="search-mismatch",
                holder_id="main-bot",
                relay_turn=turn,
                progress_reason="driver 与候选不一致",
                payload={
                    "outcome": "HIT_SINGLE",
                    "driver_bot_id": "a-bot",
                    "next_relay_bots": ["b-bot"],
                },
            )
        )


def test_hit_multi_requires_two_plus_bots_with_driver() -> None:
    service, _ = _service()
    target, turn = _prepare_hit_target(service)

    with pytest.raises(TaskStateError, match="HIT_MULTI_BOTS requires driver_bot_id"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=target,
                event_type="DISPATCH_RESULT",
                event_id="search-lone-multi",
                holder_id="main-bot",
                relay_turn=turn,
                progress_reason="单 bot 冒充群协作",
                payload={
                    "outcome": "HIT_MULTI_BOTS",
                    "driver_bot_id": "m-bot",
                    "next_relay_bots": ["m-bot"],
                },
            )
        )


def test_unknown_dispatch_outcome_is_rejected() -> None:
    service, _ = _service()
    target, turn = _prepare_hit_target(service)

    with pytest.raises(TaskStateError, match="unsupported dispatch outcome"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=target,
                event_type="DISPATCH_RESULT",
                event_id="search-mystery",
                holder_id="main-bot",
                relay_turn=turn,
                progress_reason="未知派发结论",
                payload={"outcome": "TELEPORT"},
            )
        )


# =====================================================================
# task_service_relay_dispatch: dispatch guards
# =====================================================================


def test_dispatch_rejects_origin_that_did_not_plan_target() -> None:
    service, _ = _service()
    target, turn = _prepare_hit_target(service)

    with pytest.raises(TaskStateError, match="was not planned by origin node"):
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id=target,  # the target did not plan itself
                target_node_id=target,
                holder_id="main-bot",
                relay_turn=turn,
                dispatch_id="dispatch-unplanned",
            )
        )


def test_dispatch_rejects_non_pending_target() -> None:
    service, _ = _service()
    target, turn = _prepare_hit_target(service)
    _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id="relay-task",
            target_node_id=target,
            holder_id="main-bot",
            relay_turn=turn,
            dispatch_id="dispatch-once",
        )
    )

    with pytest.raises(TaskStateError, match="must be PENDING"):
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id="relay-task",
                target_node_id=target,
                holder_id="main-bot",
                relay_turn=turn,
                dispatch_id="dispatch-again-new-id",
            )
        )


def test_dispatch_requires_persisted_decision_on_bbs_nodes() -> None:
    service, _ = _service()
    turn = _grant_root_turn(service)
    planned = _plan_next(
        service,
        turn,
        "plan-bbs-guard",
        {"gaps": ["补齐市场研究 gap"], "next_task_spec": _child_spec()},
    )
    target = planned["target_node_id"]
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=target,
            event_type="DISPATCH_RESULT",
            event_id="miss-guard",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="无匹配候选",
            failure_reason="候选能力均不匹配",
            payload={"outcome": "MISS"},
        )
    )

    with pytest.raises(TaskStateError, match="persisted dispatch decision"):
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id="relay-task",
                target_node_id=target,
                holder_id="main-bot",
                relay_turn=turn,
                dispatch_id="dispatch-bbs",
            )
        )


def test_dispatch_retry_of_delivering_attempt_restores_ticket() -> None:
    service, graph_service = _service()
    target, turn = _prepare_hit_target(service)
    # Simulate a delivery that crashed after consuming its ticket (process died
    # between the DELIVERING patch and the delivery outcome) but whose turn is
    # still GRANTED: REOPEN raises and must be tolerated before validation.
    graph_service.update_task_node_info(
        TaskNodePatch(
            task_id="relay-task",
            node_id=target,
            extend_props_patch={
                "relay_dispatch_id": "dispatch-retry",
                "relay_dispatch_state": "DELIVERING",
            },
        )
    )

    delivered = _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id="relay-task",
            target_node_id=target,
            holder_id="main-bot",
            relay_turn=turn,
            dispatch_id="dispatch-retry",
        )
    )

    assert delivered["ok"] is True
    assert delivered["assignee"] == "research-bot-hit"
    node = next(
        n for n in graph_service.query_task_dashboard("relay-task").tasks if n.node_id == target
    )
    assert node.status == Status.RUNNING
    assert node.run_info.extend_props["relay_dispatch_state"] == "DELIVERED"


def test_dispatch_with_invalid_turn_replays_diagnostics_and_raises() -> None:
    service, _ = _service()
    target, _turn = _prepare_hit_target(service)

    with pytest.raises(TaskStateError, match="relay turn invalid"):
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id="relay-task",
                target_node_id=target,
                holder_id="main-bot",
                relay_turn="forged-ticket-value",
                dispatch_id="dispatch-forged",
            )
        )


def test_dispatch_with_turn_held_by_other_node_rejects_origin() -> None:
    service, graph_service = _service()
    target, turn = _prepare_hit_target(service)
    # Consume the origin's turn and let the target node hold a fresh lease:
    # the dispatcher origin then does not own the current turn.
    RelayCoordinator(graph_service).consume("relay-task", "relay-task", "main-bot", turn)
    foreign_turn = RelayCoordinator(graph_service).grant(
        "relay-task", target, "main-bot"
    )
    assert foreign_turn is not None

    with pytest.raises(TaskStateError, match="origin does not own current turn"):
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id="relay-task",
                target_node_id=target,
                holder_id="main-bot",
                relay_turn=foreign_turn.token,
                dispatch_id="dispatch-wrong-origin",
            )
        )


def test_dispatch_survives_runner_crash_and_reopens_turn() -> None:
    service, graph_service = _service()
    target, turn = _prepare_hit_target(service)

    async def _exploding_start_run(_nodes):
        raise RuntimeError("runner exploded mid dispatch")

    original = service._runner.start_run
    service._runner.start_run = _exploding_start_run
    try:
        with pytest.raises(TaskStateError, match="relay dispatch failed"):
            _run(
                service.dispatch_task(
                    task_id="relay-task",
                    origin_node_id="relay-task",
                    target_node_id=target,
                    holder_id="main-bot",
                    relay_turn=turn,
                    dispatch_id="dispatch-crash",
                )
            )
    finally:
        del service._runner.start_run
        del original

    node = next(
        n for n in graph_service.query_task_dashboard("relay-task").tasks if n.node_id == target
    )
    assert node.status == Status.PENDING
    assert node.run_info.failure_reason == "下一棒 Runner 派发失败"
    assert node.run_info.extend_props["relay_dispatch_state"] is None
    # The ticket was reopened, so the same dispatch_id can retry.
    graph = graph_service.query_task_dashboard("relay-task")
    assert graph.extend_props["relay_turn"]["status"] == "GRANTED"


# =====================================================================
# task_service_relay: BBS claim/report guards
# =====================================================================


def test_report_bbs_result_rejects_non_bbs_node() -> None:
    service, _ = _service()
    _run(service.execute(_request()))  # root is a RUNNING single_bot baton

    with pytest.raises(TaskStateError, match="node is not running"):
        _run(
            service.report_bbs_result(
                "relay-task", "relay-task", "main-bot", output_patch={"x": 1}
            )
        )


def test_report_bbs_result_rejects_non_claim_owner() -> None:
    service, _ = _service()
    target = _publish_bbs_target(service)
    service.claim_bbs_task("relay-task", "bbs-bot", target)

    with pytest.raises(TaskStateError, match="not claim owner"):
        _run(
            service.report_bbs_result(
                "relay-task", target, "hijacker-bot", output_patch={"x": 1}
            )
        )


def test_report_bbs_result_requires_turn_grant_invariant() -> None:
    service, _ = _service()
    target = _publish_bbs_target(service)
    service.claim_bbs_task("relay-task", "bbs-bot", target)
    service._report_relay_turn = lambda report_type, **payload: None  # hostile seam

    with pytest.raises(AssertionError, match="did not receive a turn"):
        _run(
            service.report_bbs_result(
                "relay-task", target, "bbs-bot", output_patch={"x": 1}
            )
        )