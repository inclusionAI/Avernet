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
        self.calls: list[tuple[str, bool, bool]] = []

    async def get_trajectory(
        self, task_id: str, *, do_analysis: bool = False,
        force_analysis: bool = False,
    ) -> TaskTrajectory:
        self.calls.append((task_id, do_analysis, force_analysis))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._trajectory

    # The endpoint exercises get_trajectory only; the protocol's emit_* (write
    # path) are out-of-scope here — no-op stubs so the stub structurally
    # satisfies TaskContextServiceProtocol for the injector binding.
    def emit_trajectory_event(self, *args, **kwargs) -> None:
        pass  # pragma: no cover


class _StubModule(Module):
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
    assert stub_service.calls == [("t1", False, False)]


@pytest.mark.unit
def test_trajectory_force_analysis_param_passthrough(client, stub_service):
    """``?force_analysis=true`` 查询参经路由透传到 service(内部路由镜像同理)。
    force 隐含分析触发,由 service 层承接;此处断言边界层转发忠实。"""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "true", "force_analysis": "true"},
    )
    assert r.status_code == 200
    assert stub_service.calls[-1] == ("t1", True, True)

    # 不带参数 → 两参默认 False
    r2 = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1"},
    )
    assert r2.status_code == 200
    assert stub_service.calls[-1] == ("t1", False, False)


@pytest.mark.unit
def test_trajectory_explicit_do_analysis_false(client, stub_service):
    """?do_analysis=false → same pure-read shape."""
    r = client.get(
        "/openapi/v1/collaboration/tasks/trajectory",
        params={"task_id": "t1", "do_analysis": "false"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["analysis"] is None
    assert stub_service.calls == [("t1", False, False)]


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
    assert svc.calls == [("t1", True, False)]


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
    assert svc.calls == [("t1", True, False)]


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
    assert svc.calls == [("t1", True, False)]


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
    assert svc.calls == [("t1", True, False)]


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
    assert svc.calls == [("t1", False, False)]


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
    assert svc.calls == [("t1", False, False)]


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
    assert "2023-11-15 06:13:20 北京时间" in r.text
    assert "22:13:20 北京时间" not in r.text   # not the UTC wall-clock


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
