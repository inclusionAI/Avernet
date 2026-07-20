"""HTTP-level tests for ``engine.community.api.aicoding_sessions.router``.

构建一个最小 FastAPI app 把 router 挂上来，通过 monkeypatch 替换 router
内部的两个 service 工厂（``_workspace_service`` / ``_runstatus_service``）
和 ``check_capability``——这样可以脱离 ``EngineManager`` 单例与真正的引擎
插件，只关心 router 自己的"参数解析 + service 调用 + 异常→HTTP 状态码"
契约。

覆盖目标：
* 7 个 HTTP endpoint × 主要分支：
    - ``GET /file-tree``：success / 404 / 400(NotADirectory) / 400(ValueError) / 403
    - ``GET /files/preview``：success / 404 / 400(IsADir) / 400 / 403 / 413
    - ``GET /git-diff``：success / 404 / 400
    - ``GET /files/diff``：success（含 old_path）/ 404 / 400(ValueError)
    - ``GET ""``（list_sessions_with_run_status）：success / session.list 500
    - ``GET /runs``：success / 404
    - ``GET /phases``：success / 404 / 500(HTTPException 透传)
    - ``GET /runs/pull-requests``：success / 404 / 500
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# 注意：``engine.community.api.aicoding_sessions.__init__`` 里把 ``router`` 这个名字绑到了
# APIRouter 对象上，覆盖了同名的子模块属性。所以 ``import
# engine.community.api.aicoding_sessions.router as router_mod`` 拿到的会是 APIRouter,
# 不是模块。这里走 importlib 拿到真正的模块对象，再从中取 router。
router_mod = importlib.import_module("engine.community.api.aicoding_sessions.router")
router = router_mod.router

from engine.community.core.aicoding.models import (  # noqa: E402
    DiffTreeNode,
    FileContent,
    FileTreeNode,
    GitDiffResult,
    GitDiffTreeResult,
    GitProjectDiff,
)
from engine.community.core.aicoding.workspace_service import (  # noqa: E402
    FilePreviewTooLargeError,
)


# ── fakes ───────────────────────────────────────────────────────────────────


@dataclass
class FakeWorkspaceService:
    """记录调用 + 受配置驱动返回 / 抛错的 ``WorkspaceService`` 替身。"""

    list_file_tree_return: Any = None
    list_file_tree_raise: Optional[BaseException] = None
    preview_file_return: Any = None
    preview_file_raise: Optional[BaseException] = None
    list_git_diff_return: Any = None
    list_git_diff_raise: Optional[BaseException] = None
    get_file_diff_return: Any = None
    get_file_diff_raise: Optional[BaseException] = None
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def list_file_tree(
        self, session_id: str | None, cwd: str | None = None
    ):
        self.calls.append(("list_file_tree", {"session_id": session_id, "cwd": cwd}))
        if self.list_file_tree_raise:
            raise self.list_file_tree_raise
        return self.list_file_tree_return or []

    async def preview_file(self, session_id: str, path: str, cwd: str | None = None):
        self.calls.append(
            ("preview_file", {"session_id": session_id, "path": path, "cwd": cwd})
        )
        if self.preview_file_raise:
            raise self.preview_file_raise
        return self.preview_file_return

    async def list_git_diff(self, session_id: str, cwd: str | None = None):
        self.calls.append(("list_git_diff", {"session_id": session_id, "cwd": cwd}))
        if self.list_git_diff_raise:
            raise self.list_git_diff_raise
        return self.list_git_diff_return

    async def get_file_diff(self, **kwargs):
        self.calls.append(("get_file_diff", kwargs))
        if self.get_file_diff_raise:
            raise self.get_file_diff_raise
        return self.get_file_diff_return


@dataclass
class FakeRunStatusService:
    """记录调用 + 受配置驱动的 ``RunStatusService`` 替身。"""

    enrich_return: Any = None
    enrich_raise: Optional[BaseException] = None
    runs_return: Any = None
    runs_raise: Optional[BaseException] = None
    phase_return: Any = None
    phase_raise: Optional[BaseException] = None
    pr_return: Any = None
    pr_raise: Optional[BaseException] = None
    issue_return: Any = None
    issue_raise: Optional[BaseException] = None
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def enrich_with_run_status(self, sessions):
        self.calls.append(("enrich_with_run_status", {"sessions": sessions}))
        if self.enrich_raise:
            raise self.enrich_raise
        # 默认行为：每条 session 补 idle
        if self.enrich_return is not None:
            return self.enrich_return
        return [{**s, "run_status": "idle"} for s in sessions]

    async def get_session_runs(self, session_id: str, cwd: str | None = None):
        self.calls.append(("get_session_runs", {"session_id": session_id, "cwd": cwd}))
        if self.runs_raise:
            raise self.runs_raise
        return self.runs_return or []

    async def get_run_phase_status(
        self, session_id: str, run_id: str, cwd: str | None = None
    ):
        self.calls.append(
            ("get_run_phase_status", {"session_id": session_id, "run_id": run_id, "cwd": cwd})
        )
        if self.phase_raise:
            raise self.phase_raise
        return self.phase_return

    async def get_session_pull_requests(self, session_id: str, cwd: str | None = None):
        self.calls.append(
            ("get_session_pull_requests", {"session_id": session_id, "cwd": cwd})
        )
        if self.pr_raise:
            raise self.pr_raise
        return self.pr_return or []

    async def get_session_issues(self, session_id: str, cwd: str | None = None):
        self.calls.append(
            ("get_session_issues", {"session_id": session_id, "cwd": cwd})
        )
        if self.issue_raise:
            raise self.issue_raise
        return self.issue_return or []


@dataclass
class FakeSession:
    """构造 ``_session_to_dict`` 所需字段的最小 session 替身。"""

    id: str = "sess-1"
    key: str = "sess-1"
    title: Optional[str] = "Hi"
    user_id: str = "u1"
    agent_id: Optional[str] = "bot1"
    model: Optional[str] = "gpt-x"
    runtime: Optional[str] = None
    permission_mode: Optional[str] = "auto"
    cwd: Optional[str] = None
    created_at: Any = None
    updated_at: Any = None
    message_count: int = 3
    last_message: Any = None
    ext_info: dict[str, Any] | None = None


class FakeSessionService:
    """``manager.session`` 替身，仅暴露 ``list``。"""

    def __init__(self, sessions=None, raise_exc: Optional[BaseException] = None):
        self._sessions = sessions or []
        self._raise = raise_exc
        self.calls: list[Any] = []

    async def list(self, req):
        self.calls.append(req)
        if self._raise:
            raise self._raise
        return self._sessions


class FakeEngineManager:
    """``EngineManager.get_instance()`` 替身。"""

    def __init__(self, session_service=None):
        self.session = session_service or FakeSessionService()


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def workspace_svc() -> FakeWorkspaceService:
    return FakeWorkspaceService()


@pytest.fixture
def runstatus_svc() -> FakeRunStatusService:
    return FakeRunStatusService()


@pytest.fixture
def session_svc() -> FakeSessionService:
    return FakeSessionService()


@pytest.fixture
def client(
    monkeypatch,
    workspace_svc: FakeWorkspaceService,
    runstatus_svc: FakeRunStatusService,
    session_svc: FakeSessionService,
) -> TestClient:
    """组装一个挂了 router 的 FastAPI app，并完成依赖替换。

    - ``check_capability`` 默认放行（返回 None）；如需测 501 单独覆盖。
    - ``_workspace_service`` / ``_runstatus_service`` 返回 fixture 里的 fake。
    - ``EngineManager.get_instance`` 返回 fake，避免触发真单例。
    """
    monkeypatch.setattr(router_mod, "check_capability", lambda cap: None)
    monkeypatch.setattr(
        router_mod, "_workspace_service", lambda: workspace_svc,
    )
    monkeypatch.setattr(
        router_mod, "_runstatus_service", lambda: runstatus_svc,
    )

    # EngineManager 是 import 在 handler 内部的，需要替换 manager 模块本身。
    import engine.community.manager as manager_mod

    monkeypatch.setattr(
        manager_mod.EngineManager,
        "get_instance",
        classmethod(lambda cls: FakeEngineManager(session_service=session_svc)),
    )

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── /file-tree ─────────────────────────────────────────────────────────────


def test_file_tree_success(client, workspace_svc):
    workspace_svc.list_file_tree_return = [
        FileTreeNode(
            name="project-fe",
            path="project-fe",
            is_dir=True,
            children=[
                FileTreeNode(
                    name="README.md",
                    path="project-fe/README.md",
                    is_dir=False,
                    size=12,
                ),
            ],
        ),
    ]
    resp = client.get("/api/aicoding/sessions/file-tree", params={"session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["session_id"] == "s1"
    assert body["tree"][0]["name"] == "project-fe"
    assert body["tree"][0]["children"][0]["name"] == "README.md"


def test_file_tree_accepts_cwd_without_session_id(client, workspace_svc):
    workspace_svc.list_file_tree_return = []
    cwd = "/home/admin/.aicoding/workspace/direct"

    resp = client.get(
        "/api/aicoding/sessions/file-tree",
        params={"cwd": cwd},
    )

    assert resp.status_code == 200
    assert resp.json()["session_id"] is None
    assert workspace_svc.calls[-1] == (
        "list_file_tree",
        {"session_id": None, "cwd": cwd},
    )


def test_file_tree_normalizes_optional_locators(client, workspace_svc):
    workspace_svc.list_file_tree_return = []

    resp = client.get(
        "/api/aicoding/sessions/file-tree",
        params={
            "session_id": "  s1  ",
            "cwd": "  /home/admin/.aicoding/workspace/s1  ",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["session_id"] == "s1"
    assert workspace_svc.calls[-1][1] == {
        "session_id": "s1",
        "cwd": "/home/admin/.aicoding/workspace/s1",
    }


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"session_id": ""},
        {"cwd": "   "},
        {"session_id": "  ", "cwd": "\t"},
    ],
)
def test_file_tree_rejects_empty_session_id_and_cwd(
    client,
    workspace_svc,
    params,
):
    resp = client.get("/api/aicoding/sessions/file-tree", params=params)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "session_id and cwd cannot both be empty"
    assert workspace_svc.calls == []


@pytest.mark.parametrize(
    "exc, status",
    [
        (FileNotFoundError("no ws"), 404),
        (NotADirectoryError("not dir"), 400),
        (ValueError("bad"), 400),
        (PermissionError("nope"), 403),
    ],
)
def test_file_tree_error_mapping(client, workspace_svc, exc, status):
    workspace_svc.list_file_tree_raise = exc
    resp = client.get("/api/aicoding/sessions/file-tree", params={"session_id": "s1"})
    assert resp.status_code == status
    assert resp.json()["detail"] == str(exc)


# ── /files/preview ─────────────────────────────────────────────────────────


def test_files_preview_success(client, workspace_svc):
    workspace_svc.preview_file_return = FileContent(content="hi", size=2)
    resp = client.get(
        "/api/aicoding/sessions/files/preview",
        params={"session_id": "s1", "path": "a.txt"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {"content": "hi", "size": 2}


@pytest.mark.parametrize(
    "exc, status",
    [
        (FileNotFoundError("no"), 404),
        (IsADirectoryError("is dir"), 400),
        (ValueError("path is required"), 400),
        (PermissionError("nope"), 403),
        (FilePreviewTooLargeError("too big"), 413),
    ],
)
def test_files_preview_error_mapping(client, workspace_svc, exc, status):
    workspace_svc.preview_file_raise = exc
    resp = client.get(
        "/api/aicoding/sessions/files/preview",
        params={"session_id": "s1", "path": "a.txt"},
    )
    assert resp.status_code == status
    assert resp.json()["detail"] == str(exc)


# ── /git-diff ──────────────────────────────────────────────────────────────


def test_git_diff_success(client, workspace_svc):
    workspace_svc.list_git_diff_return = GitDiffTreeResult(
        session_id="s1",
        diff_head=[
            GitProjectDiff(
                project="project-fe",
                tree=DiffTreeNode(
                    name=".",
                    path="",
                    is_dir=True,
                    children=[
                        DiffTreeNode(
                            name="README.md",
                            path="README.md",
                            is_dir=False,
                            status="modified",
                        )
                    ],
                ),
            )
        ],
    )
    resp = client.get("/api/aicoding/sessions/git-diff", params={"session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["diff_head"][0]["project"] == "project-fe"
    assert body["diff_head"][0]["tree"]["children"][0]["status"] == "modified"


@pytest.mark.parametrize(
    "exc, status",
    [
        (FileNotFoundError("no ws"), 404),
        (ValueError("bad"), 400),
    ],
)
def test_git_diff_error_mapping(client, workspace_svc, exc, status):
    workspace_svc.list_git_diff_raise = exc
    resp = client.get("/api/aicoding/sessions/git-diff", params={"session_id": "s1"})
    assert resp.status_code == status


# ── /files/diff ────────────────────────────────────────────────────────────


def test_files_diff_success_with_old_path(client, workspace_svc):
    workspace_svc.get_file_diff_return = GitDiffResult(
        session_id="s1",
        project="project-fe",
        path="new.md",
        diff="diff body",
    )
    resp = client.get(
        "/api/aicoding/sessions/files/diff",
        params={
            "session_id": "s1",
            "project": "project-fe",
            "path": "new.md",
            "old_path": "old.md",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["diff"] == "diff body"
    assert body["path"] == "new.md"
    # router 必须把 path/old_path 透传给 service
    call_kwargs = workspace_svc.calls[-1][1]
    assert call_kwargs["file_path"] == "new.md"
    assert call_kwargs["old_path"] == "old.md"


@pytest.mark.parametrize(
    "exc, status",
    [
        (FileNotFoundError("no project"), 404),
        (ValueError("Not a git repository: foo"), 400),
        (ValueError("invalid project: '..'"), 400),
    ],
)
def test_files_diff_error_mapping(client, workspace_svc, exc, status):
    workspace_svc.get_file_diff_raise = exc
    resp = client.get(
        "/api/aicoding/sessions/files/diff",
        params={"session_id": "s1", "project": "p", "path": "x"},
    )
    assert resp.status_code == status


# ── GET ""（list_sessions_with_run_status） ─────────────────────────────────


def test_list_sessions_with_run_status_success(
    client, session_svc, runstatus_svc,
):
    """正常路径：拉 sessions → enrich 加 run_status → 返回。"""
    session_svc._sessions = [FakeSession(id="s1")]
    runstatus_svc.enrich_return = [
        {"id": "s1", "title": "Hi", "user_id": "u1", "runtime": "codefuse-antcc", "run_status": "running"}
    ]
    resp = client.get(
        "/api/aicoding/sessions",
        params={"user_id": "u1", "limit": 10, "offset": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"][0]["id"] == "s1"
    assert body["data"][0]["runtime"] == "codefuse-antcc"
    assert body["data"][0]["run_status"] == "running"


def test_list_sessions_with_run_status_500_when_session_list_fails(
    client, session_svc,
):
    """session.list 抛任何异常 → 500 + detail = str(exc)。"""
    session_svc._raise = RuntimeError("db down")
    resp = client.get("/api/aicoding/sessions")
    assert resp.status_code == 500
    assert "db down" in resp.json()["detail"]


def test_list_sessions_passes_query_to_session_list(client, session_svc):
    """user_id / agent_id / limit / offset 必须透传到 SessionListRequest。"""
    session_svc._sessions = []
    resp = client.get(
        "/api/aicoding/sessions",
        params={
            "user_id": "u9",
            "agent_id": "botX",
            "limit": 5,
            "offset": 7,
        },
    )
    assert resp.status_code == 200
    req = session_svc.calls[0]
    assert req.user_id == "u9"
    assert req.agent_id == "botX"
    assert req.limit == 5
    assert req.offset == 7


# ── /runs ──────────────────────────────────────────────────────────────────


def test_runs_success(client, runstatus_svc):
    runstatus_svc.runs_return = [{"id": "r-1", "isActive": True, "status": {}}]
    resp = client.get(
        "/api/aicoding/sessions/runs", params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["runs"][0]["id"] == "r-1"


def test_runs_404_when_workspace_missing(client, runstatus_svc):
    runstatus_svc.runs_raise = FileNotFoundError("no ws")
    resp = client.get(
        "/api/aicoding/sessions/runs", params={"session_id": "s1"},
    )
    assert resp.status_code == 404


# ── /phases ────────────────────────────────────────────────────────────────


def test_phases_success(client, runstatus_svc):
    runstatus_svc.phase_return = {
        "runId": "r-1",
        "workflow": "spec-to-pr",
        "currentPhase": "summarize",
    }
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={"session_id": "s1", "run_id": "r-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["runId"] == "r-1"


def test_phases_404_when_workspace_missing(client, runstatus_svc):
    runstatus_svc.phase_raise = FileNotFoundError("no ws")
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={"session_id": "s1", "run_id": "r-1"},
    )
    assert resp.status_code == 404


def test_phases_propagates_http_exception_from_service(client, runstatus_svc):
    """service 抛 HTTPException(400, 500 等) router 不应吞掉。"""
    runstatus_svc.phase_raise = HTTPException(status_code=400, detail="bad run_id")
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={"session_id": "s1", "run_id": "..."},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "bad run_id"


def test_phases_agents_passthrough_structured(client, runstatus_svc):
    """RFC 112：aix 的 per-agent rollup 透传为结构化 agents（main + subagent）。

    覆盖整链路：service dict → ``RunPhaseStatusData(**data)`` → ``response_model``
    序列化。response_model 不得裁掉 ``agents``。
    """
    runstatus_svc.phase_return = {
        "runId": "r-1",
        "workflow": "dev",
        "currentPhase": "execution",
        "phases": [
            {
                "phase": "execution",
                "status": "running",
                "attempts": 1,
                "agents": [
                    {
                        "agentId": "main",
                        "agentKind": "main",
                        "agentToken": None,
                        "toolCount": 7,
                        "tokens": 5200,
                    },
                    {
                        "agentId": "aix-implementer",
                        "agentKind": "subagent",
                        "agentToken": "tok-7f3a",
                        "startedAtUnixMs": 1733000020000,
                        "endedAtUnixMs": 1733000090000,
                        "durationMs": 70000,
                        "toolCount": 23,
                        "tokens": 18400,
                    },
                ],
            }
        ],
    }
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={"session_id": "s1", "run_id": "r-1"},
    )
    assert resp.status_code == 200
    phase = resp.json()["data"]["phases"][0]
    agents = phase["agents"]
    assert [a["agentId"] for a in agents] == ["main", "aix-implementer"]
    assert agents[0]["agentKind"] == "main"
    assert agents[1]["agentKind"] == "subagent"
    assert agents[1]["toolCount"] == 23
    assert agents[1]["durationMs"] == 70000
    # 现有字段不受影响。
    assert phase["phase"] == "execution"
    assert phase["status"] == "running"


def test_phases_unknown_future_fields_passthrough(client, runstatus_svc):
    """守住 ``extra="allow"``：aix 后续新增、本层未声明的字段（phase 级 + agent 级）
    必须原样透传。若有人把 model_config 改回默认 ignore，本用例会失败。"""
    runstatus_svc.phase_return = {
        "runId": "r-1",
        "workflow": "dev",
        "currentPhase": "execution",
        "phases": [
            {
                "phase": "execution",
                "status": "running",
                "phaseTokensTotal": 23600,  # 未来的 phase 级字段
                "agents": [
                    {
                        "agentId": "main",
                        "agentKind": "main",
                        "modelId": "claude-opus-4-8",  # 未来的 agent 级字段
                    }
                ],
            }
        ],
    }
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={"session_id": "s1", "run_id": "r-1"},
    )
    assert resp.status_code == 200
    phase = resp.json()["data"]["phases"][0]
    assert phase["phaseTokensTotal"] == 23600
    assert phase["agents"][0]["modelId"] == "claude-opus-4-8"


def test_phases_legacy_run_defaults_agents_to_empty(client, runstatus_svc):
    """老 run / 无 fan-out 时 agents 退化为空数组（恒在，不缺失）。"""
    runstatus_svc.phase_return = {
        "runId": "r-1",
        "workflow": "dev",
        "currentPhase": "summarize",
        "phases": [{"phase": "summarize", "status": "done"}],
    }
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={"session_id": "s1", "run_id": "r-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["phases"][0]["agents"] == []


# ── /runs/pull-requests ────────────────────────────────────────────────────


def test_pull_requests_success(client, runstatus_svc):
    runstatus_svc.pr_return = [
        {
            "runId": "r-abc-1",
            "kind": "pull-request",
            "provider": "antcode",
            "url": "https://code.example.com/x/y/pull_requests/1",
            "title": "first PR",
            "at": 1000,
            "projectDir": "/home/admin/.aicoding/user/u1/session/s1",
        }
    ]
    resp = client.get(
        "/api/aicoding/sessions/runs/pull-requests",
        params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pull_requests"][0]["url"].endswith("/1")
    assert body["pull_requests"][0]["title"] == "first PR"
    assert body["pull_requests"][0]["runId"] == "r-abc-1"
    assert body["pull_requests"][0]["projectDir"] == "/home/admin/.aicoding/user/u1/session/s1"


def test_pull_requests_404_when_workspace_missing(client, runstatus_svc):
    runstatus_svc.pr_raise = FileNotFoundError("no ws")
    resp = client.get(
        "/api/aicoding/sessions/runs/pull-requests",
        params={"session_id": "s1"},
    )
    assert resp.status_code == 404


def test_pull_requests_propagates_500_on_aix_failure(client, runstatus_svc):
    """service 抛 HTTPException(500) → router 透传。"""
    runstatus_svc.pr_raise = HTTPException(
        status_code=500, detail="aix run output list failed: boom"
    )
    resp = client.get(
        "/api/aicoding/sessions/runs/pull-requests",
        params={"session_id": "s1"},
    )
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]


# ── /runs/issues ────────────────────────────────────────────────────────────


def test_issues_success(client, runstatus_svc):
    runstatus_svc.issue_return = [
        {
            "runId": "r-issue-1",
            "kind": "issue",
            "provider": "generic",
            "url": (
                "https://issues.example.com/"
                "work-items/2026071700117528182"
            ),
            "title": "评测实例增加字段：运行时长",
            "at": 1784295500923,
            "projectDir": (
                "/home/admin/.aicoding/workspace/"
                "07644ab3-1f06-4516-93c4-a68e0b0485f7"
            ),
        }
    ]
    resp = client.get(
        "/api/aicoding/sessions/runs/issues",
        params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["issues"][0]["kind"] == "issue"
    assert body["issues"][0]["provider"] == "generic"
    assert body["issues"][0]["runId"] == "r-issue-1"
    assert body["issues"][0]["projectDir"].endswith(
        "07644ab3-1f06-4516-93c4-a68e0b0485f7"
    )
    assert runstatus_svc.calls[-1] == (
        "get_session_issues",
        {"session_id": "s1", "cwd": None},
    )


def test_issues_propagates_500_on_aix_failure(client, runstatus_svc):
    runstatus_svc.issue_raise = HTTPException(
        status_code=500, detail="aix run output list failed: boom"
    )
    resp = client.get(
        "/api/aicoding/sessions/runs/issues",
        params={"session_id": "s1"},
    )
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]


# ── _workspace_service / _runstatus_service 工厂 ───────────────────────────


def test_workspace_service_factory_uses_engine_manager(monkeypatch):
    """``_workspace_service`` 必须从 ``EngineManager.get_instance()`` 拿 file/bash。"""
    import engine.community.manager as manager_mod

    file_marker = object()
    bash_marker = object()

    class _M:
        file = file_marker
        bash = bash_marker

    monkeypatch.setattr(
        manager_mod.EngineManager,
        "get_instance",
        classmethod(lambda cls: _M()),
    )
    svc = router_mod._workspace_service()
    assert svc._file is file_marker
    assert svc._bash is bash_marker


def test_runstatus_service_factory_uses_engine_manager(monkeypatch):
    """``_runstatus_service`` 必须从 ``EngineManager.get_instance()`` 拿 bash。"""
    import engine.community.manager as manager_mod

    bash_marker = object()

    class _M:
        bash = bash_marker

    monkeypatch.setattr(
        manager_mod.EngineManager,
        "get_instance",
        classmethod(lambda cls: _M()),
    )
    svc = router_mod._runstatus_service()
    assert svc._bash is bash_marker


# ── /worktree-status ──────────────────────────────────────────────────────


def test_worktree_status_file_not_exists(client, tmp_path, monkeypatch):
    """文件不存在 → exists=False, status="idle"。"""
    from engine.community.core.aicoding import workspace_service as ws_mod

    monkeypatch.setattr(
        ws_mod.WorkspaceService, "resolve_workspace", staticmethod(lambda sid, cwd=None: str(tmp_path))
    )
    resp = client.get(
        "/api/aicoding/sessions/worktree-status", params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["session_id"] == "s1"
    assert body["exists"] is False
    assert body["status"] == "idle"


def test_worktree_status_completed(client, tmp_path, monkeypatch):
    """文件存在且 status=completed → exists=True, status="completed"。"""
    import json

    from engine.community.core.aicoding import workspace_service as ws_mod

    worktree_file = tmp_path / ".worktree.json"
    worktree_file.write_text(json.dumps({"schemaVersion": 1, "status": "completed"}))

    monkeypatch.setattr(
        ws_mod.WorkspaceService, "resolve_workspace", staticmethod(lambda sid, cwd=None: str(tmp_path))
    )
    resp = client.get(
        "/api/aicoding/sessions/worktree-status", params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["status"] == "completed"


def test_worktree_status_running(client, tmp_path, monkeypatch):
    """文件存在且 status=running → exists=True, status="running"。"""
    import json

    from engine.community.core.aicoding import workspace_service as ws_mod

    worktree_file = tmp_path / ".worktree.json"
    worktree_file.write_text(json.dumps({"schemaVersion": 1, "status": "running"}))

    monkeypatch.setattr(
        ws_mod.WorkspaceService, "resolve_workspace", staticmethod(lambda sid, cwd=None: str(tmp_path))
    )
    resp = client.get(
        "/api/aicoding/sessions/worktree-status", params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["status"] == "running"


def test_worktree_status_invalid_json(client, tmp_path, monkeypatch):
    """文件存在但 JSON 无效 → exists=True, status="idle"。"""
    from engine.community.core.aicoding import workspace_service as ws_mod

    worktree_file = tmp_path / ".worktree.json"
    worktree_file.write_text("not valid json {{{")

    monkeypatch.setattr(
        ws_mod.WorkspaceService, "resolve_workspace", staticmethod(lambda sid, cwd=None: str(tmp_path))
    )
    resp = client.get(
        "/api/aicoding/sessions/worktree-status", params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["status"] == "idle"


def test_worktree_status_missing_status_field(client, tmp_path, monkeypatch):
    """文件存在但缺少 status 字段 → exists=True, status="idle"。"""
    import json

    from engine.community.core.aicoding import workspace_service as ws_mod

    worktree_file = tmp_path / ".worktree.json"
    worktree_file.write_text(json.dumps({"schemaVersion": 1}))

    monkeypatch.setattr(
        ws_mod.WorkspaceService, "resolve_workspace", staticmethod(lambda sid, cwd=None: str(tmp_path))
    )
    resp = client.get(
        "/api/aicoding/sessions/worktree-status", params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["status"] == "idle"


# ── cwd 直传（新增可选参数）─────────────────────────────────────────────────


def test_file_tree_forwards_cwd_to_service(client, workspace_svc):
    """cwd 直传必须透传到 ``service.list_file_tree(session_id, cwd)``。"""
    workspace_svc.list_file_tree_return = []
    resp = client.get(
        "/api/aicoding/sessions/file-tree",
        params={"session_id": "s1", "cwd": "/home/admin/.aicoding/workspace/s1"},
    )
    assert resp.status_code == 200
    assert workspace_svc.calls[-1][1]["cwd"] == "/home/admin/.aicoding/workspace/s1"


def test_file_tree_cwd_missing_falls_back_to_none_cwd(client, workspace_svc):
    """不传 cwd → service 收到 cwd=None（旧逻辑兜底，完全向后兼容）。"""
    workspace_svc.list_file_tree_return = []
    resp = client.get(
        "/api/aicoding/sessions/file-tree", params={"session_id": "s1"},
    )
    assert resp.status_code == 200
    assert workspace_svc.calls[-1][1]["cwd"] is None


@pytest.mark.parametrize(
    "exc, status",
    [
        (ValueError("cwd not allowed"), 400),
        (NotADirectoryError("cwd not a directory"), 400),
        (FileNotFoundError("cwd not found"), 404),
    ],
)
def test_file_tree_cwd_validation_errors_mapped(client, workspace_svc, exc, status):
    """cwd 校验失败三类异常 → 400/404（file-tree 端点，覆盖 §2.2）。"""
    workspace_svc.list_file_tree_raise = exc
    resp = client.get(
        "/api/aicoding/sessions/file-tree",
        params={"session_id": "s1", "cwd": "/whatever"},
    )
    assert resp.status_code == status


def test_files_preview_forwards_cwd(client, workspace_svc):
    workspace_svc.preview_file_return = FileContent(content="hi", size=2)
    resp = client.get(
        "/api/aicoding/sessions/files/preview",
        params={
            "session_id": "s1",
            "path": "a.txt",
            "cwd": "/home/admin/.aicoding/workspace/s1",
        },
    )
    assert resp.status_code == 200
    assert workspace_svc.calls[-1][1]["cwd"] == "/home/admin/.aicoding/workspace/s1"


@pytest.mark.parametrize(
    "exc, status",
    [
        (NotADirectoryError("cwd not a directory"), 400),
        (PermissionError("denied"), 403),
    ],
)
def test_git_diff_cwd_new_branches(client, workspace_svc, exc, status):
    """git-diff 新增的 except 分支（设计 §2.3.3 标「新增」）。"""
    workspace_svc.list_git_diff_raise = exc
    resp = client.get(
        "/api/aicoding/sessions/git-diff",
        params={"session_id": "s1", "cwd": "/x"},
    )
    assert resp.status_code == status


def test_files_diff_cwd_new_branches(client, workspace_svc):
    """files/diff 新增 NotADirectoryError→400 / PermissionError→403。"""
    workspace_svc.get_file_diff_raise = NotADirectoryError("cwd not a dir")
    resp = client.get(
        "/api/aicoding/sessions/files/diff",
        params={"session_id": "s1", "project": "p", "path": "x", "cwd": "/x"},
    )
    assert resp.status_code == 400


def test_runs_forwards_cwd(client, runstatus_svc):
    runstatus_svc.runs_return = []
    resp = client.get(
        "/api/aicoding/sessions/runs",
        params={"session_id": "s1", "cwd": "/home/admin/.aicoding/workspace/s1"},
    )
    assert resp.status_code == 200
    assert runstatus_svc.calls[-1][1]["cwd"] == "/home/admin/.aicoding/workspace/s1"


@pytest.mark.parametrize(
    "exc, status",
    [
        (ValueError("cwd not allowed"), 400),
        (NotADirectoryError("cwd not a directory"), 400),
    ],
)
def test_runs_cwd_validation_errors(client, runstatus_svc, exc, status):
    runstatus_svc.runs_raise = exc
    resp = client.get(
        "/api/aicoding/sessions/runs", params={"session_id": "s1", "cwd": "/x"},
    )
    assert resp.status_code == status


def test_phases_forwards_cwd(client, runstatus_svc):
    runstatus_svc.phase_return = {
        "runId": "r-1",
        "workflow": "spec-to-pr",
        "currentPhase": "summarize",
    }
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={
            "session_id": "s1",
            "run_id": "r-1",
            "cwd": "/home/admin/.aicoding/workspace/s1",
        },
    )
    assert resp.status_code == 200
    assert runstatus_svc.calls[-1][1]["cwd"] == "/home/admin/.aicoding/workspace/s1"


def test_phases_cwd_validation_400(client, runstatus_svc):
    runstatus_svc.phase_raise = ValueError("cwd not allowed")
    resp = client.get(
        "/api/aicoding/sessions/phases",
        params={"session_id": "s1", "run_id": "r-1", "cwd": "/x"},
    )
    assert resp.status_code == 400


def test_pull_requests_forwards_cwd(client, runstatus_svc):
    runstatus_svc.pr_return = []
    resp = client.get(
        "/api/aicoding/sessions/runs/pull-requests",
        params={"session_id": "s1", "cwd": "/home/admin/.aicoding/workspace/s1"},
    )
    assert resp.status_code == 200
    assert runstatus_svc.calls[-1][1]["cwd"] == "/home/admin/.aicoding/workspace/s1"


def test_pull_requests_cwd_validation_400(client, runstatus_svc):
    """pull-requests 走 resolve_workspace（仅前缀校验）→ ValueError→400；
    不抛 NotADirectoryError/FileNotFoundError（§2.3.4）。"""
    runstatus_svc.pr_raise = ValueError("cwd not allowed")
    resp = client.get(
        "/api/aicoding/sessions/runs/pull-requests",
        params={"session_id": "s1", "cwd": "/x"},
    )
    assert resp.status_code == 400


def test_worktree_status_forwards_cwd_to_resolve_workspace(
    client, tmp_path, monkeypatch
):
    """cwd 直传必须透传到 resolve_workspace(session_id, cwd)。"""
    from engine.community.core.aicoding import workspace_service as ws_mod

    captured: dict = {}

    def _capture(sid, cwd=None):
        captured["sid"] = sid
        captured["cwd"] = cwd
        return str(tmp_path)

    monkeypatch.setattr(
        ws_mod.WorkspaceService, "resolve_workspace", staticmethod(_capture)
    )
    # tmp_path 下无 .worktree.json → exists:false，但仍 200
    resp = client.get(
        "/api/aicoding/sessions/worktree-status",
        params={"session_id": "s1", "cwd": "/home/admin/.aicoding/workspace/s1"},
    )
    assert resp.status_code == 200
    assert captured["sid"] == "s1"
    assert captured["cwd"] == "/home/admin/.aicoding/workspace/s1"


def test_worktree_status_invalid_cwd_returns_exists_false(
    client, monkeypatch
):
    """cwd 越界 → resolve_workspace 抛 ValueError → 兜成 exists:false/200（§2.3.3）。"""
    from engine.community.core.aicoding import workspace_service as ws_mod

    def _raise(sid, cwd=None):
        raise ValueError("cwd not allowed")

    monkeypatch.setattr(
        ws_mod.WorkspaceService, "resolve_workspace", staticmethod(_raise)
    )
    resp = client.get(
        "/api/aicoding/sessions/worktree-status",
        params={"session_id": "s1", "cwd": "/etc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["status"] == "idle"
