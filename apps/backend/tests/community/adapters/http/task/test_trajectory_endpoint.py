"""E2e tests for ``GET /tasks/{task_id}/trajectory`` (REQ-8, P5b endpoint).

Covers both the public OpenAPI mirror (``/openapi/v1/collaboration/tasks/trajectory``)
and the internal副本 (``/api/v1/collaboration/tasks/trajectory``) via the shared
``TestClient`` + ``attach_injector`` pattern (mirrors
``test_dashboard_assignee_bot_info.py``). The ``TaskContextServiceProtocol``
is stubbed at the injector level (a ``_StubModule``) so the assertions exercise
the router → service → DTO translation, not the real assembler/analyzer/bot.

Cases (spec REQ-8/REQ-10 e2e — success / interface_error / timeout /
not-configured, all via ``do_analysis=true``):
* default ``do_analysis=false`` — DTO with ``analysis=null`` for never-analyzed;
  no write (stub never receives ``do_analysis=True``).
* ``do_analysis=true`` success — DTO with ``analysis`` non-null (the JSON the
  stub returned).
* ``do_analysis=true`` bot-failure/timeout — ``TrajectoryAnalysisError`` →
  HTTP 504 + ``analysis`` unchanged (no backfill; stub raised).
* ``do_analysis=true`` bot-not-configured —
  ``TrajectoryAnalysisNotConfiguredError`` → HTTP 503.
* internal router mirror returns the same DTO shape.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.task.router import (
    router as task_router,
)
from agentclaw.community.adapters.http.task.router import (
    router as task_internal_router,
)
from agentclaw.community.api.task.task_context_service import (
    TaskContextServiceProtocol,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    TrajectoryAnalysisError,
    TrajectoryAnalysisNotConfiguredError,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)


# ---------------------------------------------------------------------------
# Stub service — configurable per-test (records calls; scripts response/error)
# ---------------------------------------------------------------------------


class _StubTrajectoryService:
    """Stub ``TaskContextServiceProtocol`` capturing ``get_trajectory`` calls."""

    def __init__(
        self,
        *,
        trajectory: TaskTrajectory | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._trajectory = trajectory
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, bool, bool, bool]] = []

    async def get_trajectory(
        self, task_id: str, *, do_analysis: bool = False,
        force_analysis: bool = False, session_detail: bool = False,
    ) -> TaskTrajectory:
        self.calls.append((task_id, do_analysis, force_analysis, session_detail))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._trajectory

    # The endpoint exercises get_trajectory only; the protocol's emit_* (write
    # path) are out-of-scope here — no-op stubs so the stub structurally
    # satisfies TaskContextServiceProtocol for the injector binding.
    def emit_trajectory_event(self, *args, **kwargs) -> None:
        pass  # pragma: no cover


class _StubModule(Module):
    @singleton
    @provider
    def artifact_repo(self) -> TaskArtifactRepositoryProtocol:
        class _EmptyArtifactRepo:
            def list_by_task(self, task_id, *, limit=200):
                return []

            def list_by_task_node(self, task_id, node_id, *, limit=100):
                return []

        return _EmptyArtifactRepo()
    """Binds ``TaskContextServiceProtocol`` to a stub instance (injector-level
    override, mirroring ``test_dashboard_assignee_bot_info.py``'s ``_StubModule``)."""

    def __init__(self, service: _StubTrajectoryService) -> None:
        super().__init__()
        self._service = service

    @singleton
    @provider
    def trajectory_service(self) -> TaskContextServiceProtocol:
        return self._service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_trajectory(*, analysis: str | None = None) -> TaskTrajectory:
    return TaskTrajectory(
        task_id="t1",
        gmt_create=1000,
        gmt_modified=1000,
        timeline=[
            TrajectoryEvent(
                task_id="t1",
                node_id="n1",
                action_type=TrajectoryActionType.SUBMIT,
                action_result="success",
                attempt=0,
                gmt_create=1000,
                gmt_modified=1000,
            ),
            TrajectoryEvent(
                task_id="t1",
                node_id="n1",
                action_type=TrajectoryActionType.DISPATCH,
                action_result="hit_single",
                attempt=0,
                gmt_create=2000,
                gmt_modified=2000,
                boost_reason="候选能力匹配，选择 Bot bot-a",
                holder_id="bot-a",
            ),
        ],
        analysis=analysis,
    )


def _analysis_json() -> str:
    return json.dumps(
        {
            "analysis_type": "tc_bot",
            "analysis_executor": "bot-traj-analyst",
            "analysis_input": "events=2",
            "analysis_output": "boost_reason: x",
            "gmt_create": 5000,
            "boost_reason": "策略=search 选中=botA(hit_single)",
            "failure_reason": None,
        },
        ensure_ascii=False,
    )


def _build_client(service: _StubTrajectoryService) -> TestClient:
    injector = Injector([_StubModule(service)])
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "traj-owner"}
    attach_injector(app, injector)
    return TestClient(app)


@pytest.fixture
def stub_service():
    return _StubTrajectoryService(trajectory=_make_trajectory(analysis=None))


@pytest.fixture
def client(stub_service):
    return _build_client(stub_service)


# ---------------------------------------------------------------------------
# do_analysis=false (default, pure read)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_default_read_returns_dto_with_null_analysis(client, stub_service):
    """GET /trajectory (default do_analysis=false) → 200, DTO, analysis=null."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["task_id"] == "t1"
    assert data["analysis"] is None
    assert len(data["timeline"]) == 2
    assert data["timeline"][0]["action_type"] == "submit"
    assert data["timeline"][1]["action_type"] == "dispatch"
    # stub received do_analysis=False (default)
    assert stub_service.calls == [("t1", False, False, False)]


@pytest.mark.unit
def test_trajectory_session_detail_param_passthrough(client, stub_service):
    """``?session_detail=true`` 查询参经路由透传到 service;缺省默认 False
    (纯读不实拉会话明细)。内部路由镜像同理(改其一须同步)。"""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "session_detail": "true"},
    )
    assert r.status_code == 200
    assert stub_service.calls[-1] == ("t1", False, False, True)


@pytest.mark.unit
def test_trajectory_force_analysis_param_passthrough(client, stub_service):
    """``?force_analysis=true`` 查询参经路由透传到 service(内部路由镜像同理)。
    force 隐含分析触发,由 service 层承接;此处断言边界层转发忠实。"""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true", "force_analysis": "true"},
    )
    assert r.status_code == 200
    assert stub_service.calls[-1] == ("t1", True, True, False)

    # 不带参数 → 两参默认 False
    r2 = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1"},
    )
    assert r2.status_code == 200
    assert stub_service.calls[-1] == ("t1", False, False, False)


@pytest.mark.unit
def test_trajectory_explicit_do_analysis_false(client, stub_service):
    """?do_analysis=false → same pure-read shape."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "false"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["analysis"] is None
    assert stub_service.calls == [("t1", False, False, False)]


@pytest.mark.unit
def test_trajectory_surfaces_persisted_analysis(client):
    """A persisted analysis (never-analyzed head still carries it) is surfaced."""
    persisted = _analysis_json()
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=persisted))
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["analysis"] == persisted


# ---------------------------------------------------------------------------
# do_analysis=true success
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_do_analysis_true_returns_fresh_analysis():
    """?do_analysis=true success → 200, DTO.analysis = fresh JSON from service."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=_analysis_json())
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["analysis"] is not None
    parsed = json.loads(data["analysis"])
    assert parsed["analysis_type"] == "tc_bot"
    assert parsed["analysis_executor"] == "bot-traj-analyst"
    assert svc.calls == [("t1", True, False, False)]


# ---------------------------------------------------------------------------
# do_analysis=true bot failure/timeout → 504
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_do_analysis_true_bot_failure_returns_504():
    """Bot failure → TrajectoryAnalysisError → HTTP 504 (决策 #14)."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=None),
        raise_exc=TrajectoryAnalysisError("bot timed out"),
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 504
    assert svc.calls == [("t1", True, False, False)]


@pytest.mark.unit
def test_trajectory_do_analysis_true_bot_timeout_returns_504():
    """Bot timeout is the same TrajectoryAnalysisError → HTTP 504."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=None),
        raise_exc=TrajectoryAnalysisError("bot call timeout after 180s"),
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 504


# ---------------------------------------------------------------------------
# do_analysis=true bot not configured → 503
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_do_analysis_true_not_configured_returns_503():
    """Bot not configured → TrajectoryAnalysisNotConfiguredError → HTTP 503."""
    svc = _StubTrajectoryService(
        trajectory=_make_trajectory(analysis=None),
        raise_exc=TrajectoryAnalysisNotConfiguredError("bot_id not configured"),
    )
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 503
    assert svc.calls == [("t1", True, False, False)]


# ---------------------------------------------------------------------------
# Internal router mirror (same DTO shape, no PrincipalDep)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_internal_trajectory_mirror_returns_same_dto_shape():
    """GET /api/v1/collaboration/tasks/trajectory (internal副本) → same DTO."""
    # internal router has no PrincipalDep; the auth override on the app is
    # harmless for the internal route which doesn't declare principal.
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=_analysis_json()))
    c = _build_client(svc)
    r = c.get(
        "/api/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["task_id"] == "t1"
    assert json.loads(data["analysis"])["analysis_type"] == "tc_bot"
    assert svc.calls == [("t1", True, False, False)]


@pytest.mark.unit
def test_internal_trajectory_default_read():
    """Internal副本 default read → DTO with analysis=null."""
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=None))
    c = _build_client(svc)
    r = c.get(
        "/api/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["analysis"] is None
    assert svc.calls == [("t1", False, False, False)]


# ---------------------------------------------------------------------------
# DTO shape — flat fields, enums as .value strings, no ext_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_dto_has_flat_event_fields_no_ext_info(client):
    """The event DTO carries the flat TrajectoryEvent fields and NO ext_info
    (REQ-1: ext_info is not on the domain object)."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.status_code == 200
    ev = r.json()["data"]["timeline"][0]
    # flat fields present
    for field in (
        "task_id", "node_id", "action_type", "action_result", "attempt",
        "gmt_create", "gmt_modified", "action_input", "status_from",
        "status_to", "error_type", "error_msg", "boost_reason",
        "holder_id", "analysis",
    ):
        assert field in ev, f"missing field {field}"
    # ext_info is NOT on the DTO (REQ-1)
    assert "ext_info" not in ev
    # action_type is the string value, not the enum object
    assert ev["action_type"] == "submit"


# ---------------------------------------------------------------------------
# display=html — human-readable HTML page (default still JSON envelope)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trajectory_display_html_returns_html_page():
    """?display=html → 200 text/html, self-contained page rendering the timeline +
    the persisted analysis (boost_reason surfaced). Default JSON shape unchanged elsewhere."""
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=_analysis_json()))
    c = _build_client(svc)
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "<html" in body.lower() and "</html>" in body.lower()
    assert "t1" in body                          # task_id
    assert "submit" in body and "dispatch" in body  # action_types rendered
    assert "hit_single" in body                  # action_result chip
    assert "推进原因" in body
    assert "候选能力匹配，选择 Bot bot-a" in body
    assert "执行人" in body
    assert "bot-a" in body
    # the persisted analysis content is surfaced (boost_reason from _analysis_json)
    assert "策略=search 选中=botA(hit_single)" in body
    assert "推进理由" in body                     # analysis section label
    # the service still received do_analysis=False (display does not toggle analysis)
    assert svc.calls == [("t1", False, False, False)]


@pytest.mark.unit
def test_trajectory_display_html_internal_router_mirror():
    """Internal /api/v1 copy mirrors display=html(改其一须同步);no analysis → empty hint."""
    svc = _StubTrajectoryService(trajectory=_make_trajectory(analysis=None))
    c = _build_client(svc)
    r = c.get(
        "/api/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<html" in r.text.lower()
    assert "t1" in r.text
    assert "未分析" in r.text                     # empty-analysis hint (do_analysis=false)


@pytest.mark.unit
def test_trajectory_display_html_escapes_dynamic_content():
    """HTML must escape action_input / error_msg from external bots/requests (XSS).
    Raw ``<script>`` / ``onerror=`` must not survive into the rendered body."""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1",
        action_type=TrajectoryActionType.EXECUTE, action_result="failed",
        attempt=1, gmt_create=3000, gmt_modified=3000,
        error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
        error_msg='boom <script>alert(1)</script> "x"',
        boost_reason='匹配 <img src=x onerror=alert(2)>',
        holder_id='bot<script>alert(3)</script>',
        action_input='payload <img src=x onerror=alert(1)>',
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=3000, gmt_modified=3000, timeline=[ev], analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    body = r.text
    after_style = body.split("</style>", 1)[-1]   # body excluding inline CSS
    assert "<script>" not in after_style
    assert "<img" not in after_style              # the tag is escaped → no <img> element forms
    # escaped forms ARE present (proves the content rendered, just safely).
    # NOTE: html.escape does not touch =/() so ``onerror=alert(1)`` survives as
    # harmless literal text inside the escaped span — the XSS vector is the TAG,
    # neutralized by escaping ``<``/``>``.
    assert "&lt;script&gt;" in body
    assert "&lt;img" in body
    assert "错误类型" in body
    assert "underlying_interface_error" in body
    assert "错误信息" in body
    assert "推进原因" in body
    assert "执行人" in body


@pytest.mark.unit
def test_trajectory_display_default_and_json_stay_json(client):
    """Omitting display, or display=json, returns the default JSON envelope (not HTML)."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"}
    )
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["data"]["task_id"] == "t1"

    r2 = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "json"},
    )
    assert r2.headers["content-type"].startswith("application/json")
    assert r2.json()["data"]["task_id"] == "t1"


@pytest.mark.unit
def test_trajectory_display_html_renders_beijing_time():
    """Times render in Beijing time (UTC+8), NOT UTC — ``gmt_*`` ms epoch is absolute;
    the page must show the wall-clock the operator sees (北京时间, not 8h behind)."""
    # 1_700_000_000_000 ms = 2023-11-14 22:13:20 UTC = 2023-11-15 06:13:20 北京时间
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.SUBMIT,
        action_result="success", attempt=0,
        gmt_create=1_700_000_000_000, gmt_modified=1_700_000_000_000,
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1_700_000_000_000, gmt_modified=1_700_000_000_000,
        timeline=[ev], analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    # 时区仍是北京(UTC+8)换算的墙钟,但不再带“北京时间”字样
    assert "2023-11-15 06:13:20" in r.text
    assert "22:13:20" not in r.text   # not the UTC wall-clock
    assert "北京时间" not in r.text    # 字样按需求移除


@pytest.mark.unit
def test_trajectory_dto_passes_through_enriched_node_output():
    """读时富化的节点产出(仅每 node 最后一条事件携带)经 DTO 透传为
    ``timeline[i].output``;未挂载的事件为 null。"""
    events = [
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.PLAN,
            action_result="success", attempt=0, gmt_create=1000, gmt_modified=1000,
        ),
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
            action_result="success", attempt=0, gmt_create=2000, gmt_modified=2000,
            output={"result": "n1-done"},  # 富化只挂最后一条
        ),
    ]
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=events, analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1"},
    )
    assert r.status_code == 200
    timeline = r.json()["data"]["timeline"]
    assert timeline[0]["output"] is None
    assert timeline[1]["output"] == {"result": "n1-done"}


@pytest.mark.unit
def test_trajectory_dto_passes_through_actual_run_mode():
    """真实模态读时富化(仅每 node 最后一条事件携带)经 DTO 透传为
    ``timeline[i].actual_run_mode``;未挂载的事件为 null。"""
    events = [
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.PLAN,
            action_result="success", attempt=0, gmt_create=1000, gmt_modified=1000,
        ),
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
            action_result="success", attempt=0, gmt_create=2000, gmt_modified=2000,
            actual_run_mode="single_bot",  # 富化只挂最后一条
        ),
    ]
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=events, analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1"},
    )
    assert r.status_code == 200
    timeline = r.json()["data"]["timeline"]
    assert timeline[0]["actual_run_mode"] is None
    assert timeline[1]["actual_run_mode"] == "single_bot"


@pytest.mark.unit
def test_trajectory_display_html_shows_actual_run_mode_per_subtask_block():
    """HTML 子任务区块头展示该任务的真实模态(chip 取自区块末位事件的
    actual_run_mode 富化);无模态的区块不渲染 chip。"""
    events_n1 = [
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.PLAN,
            action_result="success", attempt=0, gmt_create=1000, gmt_modified=1000,
        ),
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
            action_result="success", attempt=0, gmt_create=2000, gmt_modified=2000,
            actual_run_mode="coop_group",  # 富化只挂最后一条
        ),
    ]
    events_n2 = [
        TrajectoryEvent(
            task_id="t1", node_id="n2", action_type=TrajectoryActionType.PLAN,
            action_result="success", attempt=0, gmt_create=1500, gmt_modified=1500,
        ),
    ]
    traj = TaskTrajectory(
        task_id="t1", gmt_create=2000, gmt_modified=2000,
        timeline=events_n1 + events_n2, analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    # 有模态的 n1 区块:头上一枚「真实模态 · coop_group」chip,仅此一枚
    assert '<span class="nb-mode">真实模态 · coop_group</span>' in r.text
    assert r.text.count('class="nb-mode"') == 1
    # 事件卡片内不再重复展示模态行
    assert r.text.count("真实模态") == 1


@pytest.mark.unit
def test_trajectory_dto_maps_action_name():
    """action_name(2026-10-16):action_type 的中文人读名映射——已知码取映射表,
    未知码兜底为 action_type 原文;虚拟展示事件与嵌挂子事件同样携带。"""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="S1_CONTEXT",
        action_result="completed", attempt=0, gmt_create=1000, gmt_modified=1000,
    )
    ev_fallback = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="harness-engine-reset",
        action_result="sla_timeout", attempt=0, gmt_create=1100, gmt_modified=1100,
    )
    ev_virtual = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="task_result_display",
        action_result="display", attempt=0, gmt_create=1200, gmt_modified=1200,
        system_logs=[
            TrajectoryEvent(
                task_id="t1", node_id="n1", action_type="S5A_REFRESH_GAPS",
                action_result="completed", attempt=0,
                gmt_create=1300, gmt_modified=1300,
            ),
        ],
    )
    traj = TaskTrajectory(task_id="t1", gmt_create=1200, gmt_modified=1200,
                         timeline=[ev, ev_fallback, ev_virtual], analysis=None)
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get("/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"})
    assert r.status_code == 200
    timeline = r.json()["data"]["timeline"]
    assert timeline[0]["action_name"] == "步骤1:获取任务上下文"
    assert timeline[1]["action_name"] == "harness-engine-reset", "未知码兜底原文"
    assert timeline[2]["action_name"] == "当前子任务结果及消息详情展示"
    assert timeline[2]["system_logs"][0]["action_name"] == "步骤5A:更新任务GAPs"


@pytest.mark.unit
def test_trajectory_display_html_shows_action_type_with_name():
    """HTML 徽章渲染为 ``action_type(action_name)``;兜底场景(名称=原文)只显示
    action_type 原文,避免 reset(reset) 式重复。"""
    ev_s = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="S5C_SEARCH_CANDIDATES",
        action_result="not_applicable", attempt=0, gmt_create=1000, gmt_modified=1000,
    )
    ev_fallback = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="harness-engine-reset",
        action_result="sla_timeout", attempt=0, gmt_create=2000, gmt_modified=2000,
    )
    traj = TaskTrajectory(task_id="t1", gmt_create=2000, gmt_modified=2000,
                          timeline=[ev_s, ev_fallback], analysis=None)
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    page = r.text
    assert "S5C_SEARCH_CANDIDATES(步骤5C:搜索下一棒候选者)" in page
    assert "harness-engine-reset(步骤" not in page, "兜底不产生映射"
    assert "harness-engine-reset(" not in page, "兜底不重复展示(原名即名称)"


@pytest.mark.unit
def test_trajectory_dto_passes_through_system_logs():
    """读时嵌套的 system_logs 经 DTO 递归透传:主事件携带、无嵌挂为 null、
    嵌挂子事件自身不再嵌套。"""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="S1_CONTEXT",
        action_result="completed", attempt=0, gmt_create=1000, gmt_modified=1000,
        system_logs=[
            TrajectoryEvent(
                task_id="t1", node_id="n1", action_type="execute",
                action_result="success", attempt=0,
                gmt_create=1100, gmt_modified=1100,
            ),
            TrajectoryEvent(
                task_id="t1", node_id="n1", action_type="reset",
                action_result="sla_timeout", attempt=0,
                gmt_create=1200, gmt_modified=1200,
            ),
        ],
    )
    ev_plain = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="S4A_EXECUTION",
        action_result="completed", attempt=0, gmt_create=2000, gmt_modified=2000,
    )
    traj = TaskTrajectory(task_id="t1", gmt_create=2000, gmt_modified=2000,
                         timeline=[ev, ev_plain], analysis=None)
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get("/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"})
    assert r.status_code == 200
    timeline = r.json()["data"]["timeline"]
    # 主事件携带嵌挂子事件(按 id 正序),子事件不再嵌套
    assert [s["action_type"] for s in timeline[0]["system_logs"]] == [
        "execute", "reset",
    ]
    assert timeline[0]["system_logs"][0]["system_logs"] is None
    assert timeline[1]["system_logs"] is None


@pytest.mark.unit
def test_trajectory_display_html_places_action_result_below_action_input():
    """action_result 展示位置(2026-10-16):不再在卡片头部,而是放在
    action_input 折叠块的**下方**,带 ev-label 标签 + 红/绿语义 chip。"""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="S1_CONTEXT",
        action_result="completed", attempt=0, gmt_create=1000, gmt_modified=1000,
        action_input="读取上下文原文",
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=[ev], analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    page = r.text

    # ① action_result 的标签行出现在 action_input 折叠块之后
    input_start = page.index("读取上下文原文")
    result_line = page.index('<span class="ev-label">action_result</span>')
    assert input_start < result_line, "action_result 应在 action_input 之下"

    # ② 头部不再有结果 chip 以外的 action_result 出现(ev-head 内不含 chip)
    head_start = page.index('<div class="ev-head">')
    head_end = page.index("</div>", head_start)
    head_html = page[head_start:head_end]
    assert "completed" not in head_html, "头部不再展示 action_result"
    assert 'class="chip' not in head_html


@pytest.mark.unit
def test_trajectory_display_html_renders_system_logs():
    """HTML 页在携带嵌挂的主事件下渲染可折叠「系统事件」块:
    默认收起(details 无 open 属性)、点 summary 展开、子事件逐条可见
    (类型/结果/墙钟时间/原因,按 id 正序)。"""
    import re as _re

    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type="S1_CONTEXT",
        action_result="completed", attempt=0, gmt_create=1000, gmt_modified=1000,
        system_logs=[
            TrajectoryEvent(
                task_id="t1", node_id="n1", action_type="execute",
                action_result="success", attempt=0,
                gmt_create=1100, gmt_modified=1100,
                boost_reason="回投执行",
            ),
            TrajectoryEvent(
                task_id="t1", node_id="n1", action_type="reset",
                action_result="sla_timeout", attempt=0,
                gmt_create=1200, gmt_modified=1200,
                boost_reason="harness 巡检复位",
            ),
        ],
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=[ev], analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    page = r.text
    # ① 找到 details 块:存在且默认收起(无 open 属性),summary 为点击展开控件
    m = _re.search(r"<details class=\"ev-slog\">(.*?)</details>", page, _re.S)
    assert m, "系统事件应为 <details class=ev-slog> 块"
    block = m.group(0)
    assert ' open' not in block, "默认必须收起(无 open 属性)"
    assert "<summary>系统事件 · system_logs 2 条</summary>" in block, (
        "summary 应作为展开控件并显示条数"
    )
    # ② 子事件逐条呈现:按 id 正序,含类型/结果/原因
    assert "execute" in block and "reset" in block
    assert block.index("execute") < block.index("reset"), "按 id 正序"
    assert "sla_timeout" in block and "harness 巡检复位" in block
    # ③ 每条携带墙钟时间(_fmt_time 换算自 1100/1200ms epoch)
    from agentclaw.community.adapters.http.task.trajectory_html import _fmt_time
    assert _fmt_time(1200) in block


@pytest.mark.unit
def test_trajectory_display_html_renders_enriched_node_output():
    """HTML 页在携带产出的最后一条事件下渲染可折叠的「节点产出」块。"""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
        action_result="success", attempt=0,
        gmt_create=1000, gmt_modified=1000,
        output={"result": "html-n1-done", "steps": 3},
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=[ev], analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    assert "节点产出" in r.text
    assert "html-n1-done" in r.text


@pytest.mark.unit
def test_trajectory_display_html_groups_events_by_subtask_block():
    """HTML 时间线按子任务区块化:同一 (task_id,node_id) 的事件归同一区块
    (区块序 = 该子任务首条事件出现序),不同区块左色条颜色不同。"""
    import re

    # 交错时序:n1 plan@1000 → n2 plan@1500 → n1 execute@2000 → n1 verify@2500。
    # 分组后 n1 的 3 条全部落在 n1 区块内(verify 紧跟其后),n2 自成 1 条区块。
    def _n(node_id: str, action: TrajectoryActionType, ms: int) -> TrajectoryEvent:
        return TrajectoryEvent(
            task_id="t1", node_id=node_id, action_type=action,
            action_result="success", attempt=0, gmt_create=ms, gmt_modified=ms,
        )

    traj = TaskTrajectory(
        task_id="t1", gmt_create=2500, gmt_modified=2500,
        timeline=[
            _n("n1", TrajectoryActionType.PLAN, 1000),
            _n("n2", TrajectoryActionType.PLAN, 1500),
            _n("n1", TrajectoryActionType.EXECUTE, 2000),
            _n("n1", TrajectoryActionType.VERIFY, 2500),
        ],
        analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    text = r.text

    # 两个子任务区块,每个区块开头带序号标记(接力任务1/接力任务2,按区块序)
    assert text.count('class="node-block"') == 2
    assert ">接力任务1<" in text and ">接力任务2<" in text
    assert text.index(">接力任务1<") < text.index(">接力任务2<")
    assert ">接力任务3<" not in text
    # n1 区块 3 条、n2 区块 1 条(区块副标题带事件数)
    assert "· 事件 3" in text and "· 事件 1" in text
    # 区块顺序 = 首条事件出现序:n1(@1000) 在 n2(@1500) 之前
    assert text.index("· 事件 3") < text.index("· 事件 1")
    # 分组完整性:时序上排在 n2 之后的 n1 verify 卡片仍在 n1 区块内
    # (n2 区块头之前)——即 n1 的全部事件没有被 n2 切开
    assert text.index(">verify</span>") < text.index("· 事件 1")
    # 不同区块不同颜色(循环色板)
    colors = re.findall(
        r'class="node-block" style="border-left:4px solid (#[0-9a-f]{6});"', text
    )
    assert len(colors) == 2 and colors[0] != colors[1]


@pytest.mark.unit
def test_trajectory_display_html_renders_final_status_and_error_category():
    """总体分析块渲染大模型判断的新两字段:最终执行状态 + 错误类型分类。"""
    analysis = json.dumps({
        "analysis_type": "tc_bot",
        "analysis_executor": "bot-analyst",
        "final_status": "FAILED",
        "error_category": "execution_error",
        "failure_reason": "bot 工具执行抛错",
        "analysis_output": "模态执行报错导致任务失败",
    }, ensure_ascii=False)
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
        action_result="failed", attempt=0, gmt_create=1000, gmt_modified=1000,
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=[ev], analysis=analysis,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    assert "最终执行状态 (final_status)" in r.text
    assert "FAILED" in r.text
    assert "错误类型 (error_category)" in r.text
    assert "execution_error" in r.text


@pytest.mark.unit
def test_trajectory_dto_passes_through_session_msgs_on_last_event():
    """会话消息读时富化经 DTO 透传为 ``timeline[i].session_msgs``;未携带为 null。"""
    ev1 = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.PLAN,
        action_result="success", attempt=0, gmt_create=1000, gmt_modified=1000,
    )
    ev2 = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
        action_result="success", attempt=0, gmt_create=2000, gmt_modified=2000,
        session_msgs=[{"role": "user", "content": "hi"},
                      {"role": "assistant", "content": "tool failed: boom"}],
    )
    traj = TaskTrajectory(task_id="t1", gmt_create=2000, gmt_modified=2000,
                         timeline=[ev1, ev2], analysis=None)
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get("/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"})
    assert r.status_code == 200
    timeline = r.json()["data"]["timeline"]
    assert timeline[0]["session_msgs"] is None
    assert timeline[1]["session_msgs"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "tool failed: boom"},
    ]


@pytest.mark.unit
def test_trajectory_display_html_renders_session_msgs_block():
    """HTML 页在携带会话消息的末位事件下渲染可折叠「节点会话」块(role|content 逐行)。"""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
        action_result="success", attempt=0, gmt_create=1000, gmt_modified=1000,
        session_msgs=[{"role": "user", "content": "run the job"},
                      {"role": "assistant", "content": "tool failed: boom"}],
    )
    traj = TaskTrajectory(task_id="t1", gmt_create=1000, gmt_modified=1000,
                         timeline=[ev], analysis=None)
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    assert "节点会话 · 最近消息 2 条" in r.text
    assert "user | run the job" in r.text
    assert "assistant | tool failed: boom" in r.text


@pytest.mark.unit
def test_trajectory_display_html_analysis_time_and_output_prettified():
    """总体分析展示要求:①分析时间由 ms epoch 转为日历时间(不再裸露毫秒数);
    ②结论汇总是 pre-wrap 卡片(保留换行分段,不再挤在一行 a-row)。
    HMAC 时间字段与事件线均不再出现“北京时间”字样。"""
    analysis = json.dumps({
        "analysis_type": "tc_bot",
        "analysis_executor": "bot-analyst",
        "final_status": "FAILED",
        "error_category": "execution_error",
        "failure_reason": "node c5 工具调用失败,重试耗尽后 HUNG",
        "analysis_output": "「过程复盘」\n任务先规划后派发,第二轮出现工具报错。\n「根因链」\nnode c5 的 search 工具连续返回 500。",
        "gmt_create": 1790077434283,
    }, ensure_ascii=False)
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
        action_result="failed", attempt=0, gmt_create=1000, gmt_modified=1000,
    )
    traj = TaskTrajectory(task_id="t1", gmt_create=1000, gmt_modified=1000,
                          timeline=[ev], analysis=analysis)
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    # ① 分析时间转换(1790077434283 → 北京时间日历)
    assert "1790077434283" not in r.text.split("结论汇总")[0]
    import re
    assert re.search(r"分析时间.*\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", r.text)
    # ② 结论汇总卡片在 pre-wrap 块中(不再 a-row 行)
    assert 'class="a-output"' in r.text
    assert "「根因链」" in r.text  # 结论内容完整呈现
    # ③ 无“北京时间”字样
    assert "北京时间" not in r.text


@pytest.mark.unit
def test_html_content_is_a_subset_of_json_payload():
    """内容不变式:HTML 页展示的**每个数据点**必须能在同请求的 JSON payload 里取到
    (HTML ⊆ JSON——HTML 只是同一个 TrajectoryEventDTO 的人类视图)。用全字段填充的双
    事件轨迹同时取两态,逐字段断言:唯一允许的形态变换 = 时间(ms → 墙钟)、output 转
    indent JSON、session_msgs 转 `role | content` 行、状态用 `A → B`。——先 html_escape 再比对。"""
    import html as _html

    analysis = json.dumps({
        "analysis_type": "tc_bot",
        "analysis_executor": "bot-analyst",
        "final_status": "FAILED",
        "error_category": "execution_error",
        "boost_reason": "策略=search 选中=botA",
        "failure_reason": "node c5 工具调用失败,重试耗尽后 HUNG",
        "analysis_output": "「过程复盘」\n任务第二轮出现工具报错。",
        "analysis_input": "events=2",
        "gmt_create": 1790077434283,
    }, ensure_ascii=False)
    ev_rich = TrajectoryEvent(
        task_id="t1", node_id="c5",
        action_type=TrajectoryActionType.EXECUTE,
        action_result="failed", attempt=2,
        gmt_create=1790077434000, gmt_modified=1790077434100,
        action_input="执行请求原文:调用 search 工具",
        status_from="RUNNING", status_to="FAILED",
        error_type="underlying_interface_error",
        error_msg="search tool returned 500",
        boost_reason="第二轮选 botA 复执",
        holder_id="bot-a",
        actual_run_mode="coop_group",
        output={"result": "done", "steps": 3},
        session_msgs=[{"role": "user", "content": "run the job"},
                      {"role": "assistant", "content": "tool failed: boom"}],
    )
    ev_min = TrajectoryEvent(
        task_id="t1", node_id="t1",
        action_type=TrajectoryActionType.SUBMIT,
        action_result="success", attempt=0,
        gmt_create=1790077000000, gmt_modified=1790077000000,
        action_input="目标: 完成市场分析\n验收: 给出结论",
    )
    traj = TaskTrajectory(task_id="t1", gmt_create=1790077434283,
                          gmt_modified=1790077434283,
                          timeline=[ev_min, ev_rich], analysis=analysis)
    stub = _StubTrajectoryService(trajectory=traj)
    c = _build_client(stub)
    r_json = c.get("/openapi/v1/collaboration/tasks/trajectory", params={"task_id": "t1"})
    r_html = c.get("/openapi/v1/collaboration/tasks/trajectory",
                   params={"task_id": "t1", "display": "html"})
    assert r_json.status_code == 200 and r_html.status_code == 200
    page = r_html.text
    data = r_json.json()["data"]

    def _Bij(time_val):  # ms → 墙钟(与 _fmt_time 同变换)
        return __import__("agentclaw.community.adapters.http.task.trajectory_html",
                          fromlist=["_fmt_time"])._fmt_time(int(time_val))

    # 轨迹级
    assert _html.escape(data["task_id"]) in page
    assert _Bij(data["gmt_create"]) in page and _Bij(data["gmt_modified"]) in page
    # 事件级:JSON 里的每个字段值(经其已知展示变换)在 HTML 可见
    for ev in data["timeline"]:
        for key in ("task_id", "node_id", "action_type", "action_result",
                    "action_input", "error_type", "error_msg",
                    "boost_reason", "holder_id", "status_to",
                    "actual_run_mode"):
            val = ev.get(key)
            if val:
                assert _html.escape(val) in page, f"{key}={val} 未在 HTML 展示"
        ev_mine = next(t for t in (ev_min, ev_rich) if t.node_id == ev["node_id"])
        assert (_Bij(ev["gmt_create"])) in page, "事件时间未展示"
        if ev.get("status_from") and ev.get("status_to"):
            assert f'{ev["status_from"]} → {ev["status_to"]}' in page
        if ev.get("output"):
            rendered = json.dumps(ev_mine.output, ensure_ascii=False, indent=2)
            assert _html.escape(rendered) in page, "output 未展示"
        if ev.get("session_msgs"):
            for m in ev_mine.session_msgs:
                assert _html.escape(f"{m['role']} | {m['content']}") in page
    # 分析:JSON analysis 字符串内的每个字段值在 HTML 的总体分析区可见
    parsed = json.loads(data["analysis"])
    for key in ("analysis_type", "analysis_executor", "final_status", "error_category",
                "boost_reason", "failure_reason", "analysis_output"):
        assert _html.escape(str(parsed[key])) in page, f"analysis.{key} 未在 HTML 展示"
    assert _Bij(parsed["gmt_create"]) in page, "分析时间未(格式化)展示"
    assert _html.escape(parsed["analysis_input"])[:20] in page  # 折叠摘要截断


@pytest.mark.unit
def test_trajectory_dto_passes_through_search_probe():
    """定向投影的搜推采样摘要(dispatch / relay search 事件携带)经 DTO 透传为
    ``timeline[i].search_probe``;无素材的事件为 null(两模式同形态)。"""
    probe = {
        "decision_mode": "rule",
        "tokens": ["存储", "检索"],
        "keywords": [
            {"keyword": "存储", "item_count": 2, "bot_ids": ["a:o", "b:o"],
             "failed": False},
        ],
        "raw_item_count": 2,
        "failed_keywords": [],
        "candidates": [
            {"bot_id": "rule-a", "recommend_score": 0.9, "short_profile": "pA",
             "bot_name": "A", "owner_name": "O1", "reasons": None},
        ],
        "selected_bot_ids": ["rule-a:1"],
        "dropped_bot_ids": ["rule-b:2"],
        "rule_selection_note": "hit_single_takes_first",
        "skill_response_excerpt": None,
        "miss_reason": None,
    }
    events = [
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.DISPATCH,
            action_result="hit_single", attempt=0, gmt_create=1000, gmt_modified=1000,
            search_probe=probe,
        ),
        TrajectoryEvent(
            task_id="t1", node_id="n1", action_type=TrajectoryActionType.EXECUTE,
            action_result="success", attempt=0, gmt_create=2000, gmt_modified=2000,
        ),
    ]
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=events, analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1"},
    )
    assert r.status_code == 200
    timeline = r.json()["data"]["timeline"]
    assert timeline[0]["search_probe"] == probe
    assert timeline[1]["search_probe"] is None


@pytest.mark.unit
def test_trajectory_display_html_renders_search_probe():
    """HTML 页在携带采样的派发事件下渲染可折叠的「搜推明细」块:分词关键词 /
    关键词命中 / 候选打分 / 最终匹配结果 + 失败词与截断依据(旧形态:分数直接
    挂在 candidates 上,无 candidate_evaluations —— 兜得住)。"""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.DISPATCH,
        action_result="hit_single", attempt=0, gmt_create=1000, gmt_modified=1000,
        search_probe={
            "decision_mode": "rule",
            "tokens": ["存储", "检索"],
            "keywords": [
                {"keyword": "存储", "item_count": 2, "bot_ids": ["a:o", "b:o"],
                 "failed": False},
            ],
            "raw_item_count": 2,
            "failed_keywords": ["检索"],
            "candidates": [
                {"bot_id": "rule-a", "bot_name": "存储专家", "recommend_score": 0.9},
            ],
            "selected_bot_ids": ["rule-a:1"],
            "dropped_bot_ids": ["rule-b:2"],
            "rule_selection_note": "hit_single_takes_first",
            "skill_response_excerpt": None,
            "miss_reason": None,
        },
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=[ev], analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    text = r.text
    assert "搜推明细 · rule" in text
    # 逐项可见:分词关键词 / 关键词命中 / 候选打分 / 最终匹配 + 失败词与截断依据
    assert "分词关键词: 存储, 检索" in text
    assert "关键词命中:" in text
    assert "存储: 命中 2: a:o, b:o" in text
    assert "失败关键词: 检索" in text
    assert "候选者打分(1 个):" in text
    assert "存储专家" in text
    assert "(score=0.9)" in text
    assert "最终匹配结果: rule-a:1" in text
    assert "截断丢弃: rule-b:2" in text
    assert "hit_single_takes_first" in text


@pytest.mark.unit
def test_trajectory_display_html_renders_search_probe_evaluations():
    """新形态 relay_dispatch 搜推明细:整体搜索词 + candidate_evaluations
    打分评估(score/score_reason/选中决策)按 bot_id 合并候选逐项呈现。"""
    ev = TrajectoryEvent(
        task_id="t1", node_id="n1", action_type=TrajectoryActionType.DISPATCH,
        action_result="hit_single", attempt=0, gmt_create=1000, gmt_modified=1000,
        search_probe={
            "decision_mode": "relay_dispatch",
            "query": "寻找能够产出存储行业尽调报告的专业研究 Bot",
            "origin_node_id": "node-a",
            "tokens": ["寻找", "存储", "行业", "尽调", "报告"],
            "keywords": [
                {"keyword": "存储", "item_count": 1,
                 "bot_ids": ["bot1:146836"], "failed": False},
                {"keyword": "行业", "item_count": 1,
                 "bot_ids": ["bot1:146836"], "failed": False},
            ],
            "raw_item_count": 2,
            "failed_keywords": [],
            "candidates": [
                {"bot_uuid": "bot1:146836", "bot_name": "存储行业调研Bot",
                 "bot_desc": "存储行业调研Bot", "bot_type": "personal",
                 "status": "ACTIVE"},
            ],
            "candidate_evaluations": [
                {"bot_id": "bot1:146836", "score": 92,
                 "score_reason": "Bot名称直接匹配存储行业调研领域",
                 "selected": True, "decision": "selected_as_driver",
                 "reject_reason": None},
            ],
            "selected_bot_ids": ["bot1:146836"],
            "dropped_bot_ids": [],
        },
    )
    traj = TaskTrajectory(
        task_id="t1", gmt_create=1000, gmt_modified=1000,
        timeline=[ev], analysis=None,
    )
    c = _build_client(_StubTrajectoryService(trajectory=traj))
    r = c.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "display": "html"},
    )
    assert r.status_code == 200
    text = r.text
    assert "搜推明细 · relay_dispatch" in text
    # 整体搜索词可见
    assert "整体搜索词: 寻找能够产出存储行业尽调报告的专业研究 Bot" in text
    # 每个关键词的命中 bot 列表
    assert "存储: 命中 1: bot1:146836" in text
    assert "行业: 命中 1: bot1:146836" in text
    # 候选打分结果及原因(evaluations 的 score 覆盖 candidates 自身)
    assert "bot1:146836 · 存储行业调研Bot (score=92)" in text
    assert "最终选中 ✓ · selected_as_driver" in text
    assert "打分原因: Bot名称直接匹配存储行业调研领域" in text
    # 最终匹配结果
    assert "最终匹配结果: bot1:146836" in text
