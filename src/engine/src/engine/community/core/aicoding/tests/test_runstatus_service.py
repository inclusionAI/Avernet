"""Contract tests for RunStatusService.

Covers the aix-CLI shapes consumed by API 4.1/4.2/4.3:

* ``aix run list --filter <workspace_root> --json``  (API 4.1/4.2)
* ``aix run output list --kind pull-request --json --filter <workspace_root>`` (API 4.4)
* ``find -maxdepth 2 -name .aix -type d``            (API 4.3)
* ``aix run phase status --run-id ... --json --verbose`` (API 4.3)

Plus:

* enrichment behavior (cache hit, total timeout fallback to ``None``)
* error paths: aix not installed, JSON parse failure, ``run not found``
* ``_safe_exec`` swallows :class:`FileNotFoundError` / :class:`ValueError` /
  :class:`OSError` / :class:`asyncio.TimeoutError`
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import pytest
from fastapi import HTTPException

from engine.community.core.bash.models import BashExecResult
from engine.community.core.aicoding.runstatus_service import (
    IDLE_STATUS,
    RunStatusService,
    SESSION_CACHE_TTL,
    _clear_status_cache,
    _normalize_status_kind,
)
from engine.community.core.aicoding.workspace_service import (
    CONTAINER_WORKSPACE_BASE,
    WorkspaceService,
)

# _aix_run_list_all 使用 _resolve_workspace_base() 作为 cwd，测试中用 None 做
# cwd-agnostic 匹配（FakeBashPlugin 支持），避免测试耦合具体环境变量。
_CWD_AGNOSTIC = None


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    """模块级 ``_PROCESS_STATUS_CACHE`` 跨用例共享，autouse fixture 兜住污染。"""
    _clear_status_cache()
    yield
    _clear_status_cache()


@pytest.fixture(autouse=True)
def _bypass_workspace_exists(monkeypatch):
    """Bypass real-FS check — tests don't create ``/home/admin/.aicoding/...``.

    生产代码里 ``ensure_workspace_exists`` 会对 session workspace 真做
    ``os.path.isdir`` 校验；单测 mock 的是 BashService 而不是 FS，所以
    用 autouse 把校验替换成"只返回路径"，保留 ``resolve_workspace`` 的拼接
    语义，让原有用例无须额外建临时目录就能跑。针对"workspace 不存在"
    的独立用例可以在测试内显式 monkeypatch 还原后再断言。
    """
    monkeypatch.setattr(
        WorkspaceService,
        "ensure_workspace_exists",
        staticmethod(WorkspaceService.resolve_workspace),
    )


# ── test doubles ────────────────────────────────────────────────────────────


class FakeBashPlugin:
    """Configurable BashPlugin that picks responses by ``(cmd_substr, cwd)``.

    The lookup order is:

    1. exact ``(cmd_match, cwd)`` pair
    2. ``(cmd_match, None)`` cwd-agnostic
    3. ``raise_for`` — raise the configured exception
    4. fallback — exit_code=0 stdout=""

    ``cmd_match`` matches if it appears as a substring of the executed command.
    """

    def __init__(self) -> None:
        self.responses: list[tuple[str, Optional[str], BashExecResult]] = []
        self.raise_for: list[tuple[str, Optional[str], BaseException]] = []
        self.calls: list[tuple[str, str, int]] = []

    def add(
        self,
        cmd_match: str,
        cwd: Optional[str],
        result: BashExecResult,
    ) -> None:
        self.responses.append((cmd_match, cwd, result))

    def add_raise(
        self,
        cmd_match: str,
        cwd: Optional[str],
        exc: BaseException,
    ) -> None:
        self.raise_for.append((cmd_match, cwd, exc))

    async def exec(
        self,
        cmd: str,
        cwd: str,
        timeout: int = 30,
        auth=None,  # noqa: ANN001 — match Protocol signature
    ) -> BashExecResult:
        self.calls.append((cmd, cwd, timeout))
        for cmd_match, want_cwd, exc in self.raise_for:
            if cmd_match in cmd and (want_cwd is None or want_cwd == cwd):
                raise exc
        # exact pair first
        for cmd_match, want_cwd, res in self.responses:
            if cmd_match in cmd and want_cwd == cwd:
                return res
        # cwd-agnostic
        for cmd_match, want_cwd, res in self.responses:
            if cmd_match in cmd and want_cwd is None:
                return res
        return BashExecResult(stdout="", stderr="", exit_code=0)


SESSION_ID = "user:u1:session:s1:agent:bot1"
WORKSPACE = WorkspaceService.resolve_workspace(SESSION_ID)
PROJECT_DIR = f"{WORKSPACE}/project-fe"
PROJECT_DIR_2 = f"{WORKSPACE}/project-be"


def _runs_payload(runs: list[dict]) -> str:
    return json.dumps({"runs": runs})


def _make_service(plugin: FakeBashPlugin) -> RunStatusService:
    return RunStatusService(bash_plugin=plugin)


# ── _normalize_status_kind: idle 兜底 + aix kind 原样透传 ───────────────────


@pytest.mark.parametrize(
    "raw_kind, expected",
    [
        # 已知 aix kind 全部原样透传，不再做语义合并/重命名
        ("running", "running"),
        ("awaitingLlmEvaluation", "awaitingLlmEvaluation"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("awaitingApproval", "awaitingApproval"),
        ("awaitingHuman", "awaitingHuman"),
        # 唯一例外：None（无活跃 run）收敛为 idle
        (None, IDLE_STATUS),
    ],
)
def test_normalize_status_kind_known(raw_kind, expected) -> None:
    assert _normalize_status_kind(raw_kind) == expected


def test_normalize_status_kind_unknown_passes_through() -> None:
    # 透传策略下，未知 kind 也原样返回——这里复用同一条断言保证回归。
    assert _normalize_status_kind("someFutureKind") == "someFutureKind"


async def test_enrich_passes_aix_kind_verbatim() -> None:
    """aix 的 awaitingApproval / awaitingLlmEvaluation 等 kind 必须原样透传，
    不再被映射为 waitingHuman / running。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-1",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "awaitingApproval"},
                        "startedAtUnixMs": 100,
                        "updatedAtUnixMs": 100,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    assert out[0]["run_status"] == "awaitingApproval"


# ── enrich_with_run_status: 一次 aix run list --json + 按 startedAtUnixMs 取最新 run ──


async def test_enrich_returns_latest_run_status_kind() -> None:
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-1",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 100,
                        "updatedAtUnixMs": 100,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    sessions = [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    out = await service.enrich_with_run_status(sessions)
    assert out[0]["run_status"] == "running"


async def test_enrich_picks_kind_from_only_run_regardless_of_active_flag() -> None:
    """单条 run（即使 isActive=False）也作为 latest 返回——和旧 isActive 语义对齐之处。

    这是新语义最关键的差异：之前这种 case 会被收敛成 idle，现在直接返回该 kind。
    """
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-old",
                        "projectDir": PROJECT_DIR,
                        "isActive": False,
                        "status": {"kind": "completed"},
                        "startedAtUnixMs": 50,
                        "updatedAtUnixMs": 50,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    assert out[0]["run_status"] == "completed"


async def test_enrich_idle_when_cwd_not_in_runs() -> None:
    """该 cwd 在 aix runs 里完全没出现 → idle（新语义下这是 idle 的唯一触发）."""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-other",
                        "projectDir": PROJECT_DIR_2,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 100,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    assert out[0]["run_status"] == IDLE_STATUS


async def test_enrich_aix_not_installed_returns_idle() -> None:
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(stdout="", stderr="aix: command not found", exit_code=127),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    assert out[0]["run_status"] == IDLE_STATUS


async def test_enrich_aix_command_fails_returns_idle() -> None:
    """裸 aix run list 失败（exit_code != 0）→ 全部 session 收敛为 idle."""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(stdout="", stderr="aix internal error", exit_code=1),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    assert out[0]["run_status"] == IDLE_STATUS


async def test_enrich_bad_json_returns_idle() -> None:
    """命令成功但 stdout 不是合法 JSON → 全部 session 收敛为 idle."""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(stdout="not json", stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    assert out[0]["run_status"] == IDLE_STATUS


async def test_enrich_single_subprocess_for_multiple_sessions() -> None:
    """N 个 session 只触发 1 次 aix run list（核心优化点）。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-fe",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 200,
                    },
                    {
                        "id": "r-be",
                        "projectDir": PROJECT_DIR_2,
                        "isActive": True,
                        "status": {"kind": "failed"},
                        "startedAtUnixMs": 300,
                    },
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    sessions = [
        {"id": "sid-fe", "cwd": PROJECT_DIR},
        {"id": "sid-be", "cwd": PROJECT_DIR_2},
        {"id": "sid-no-cwd", "cwd": None},
        {"id": "sid-unmatched", "cwd": f"{CONTAINER_WORKSPACE_BASE}/zzz-unknown"},
    ]
    out = await service.enrich_with_run_status(sessions)
    assert [s["run_status"] for s in out] == [
        "running",
        "failed",
        IDLE_STATUS,
        IDLE_STATUS,
    ]
    # 一次 subprocess 覆盖整批 session
    aix_calls = [c for c in plugin.calls if "aix run list" in c[0]]
    assert len(aix_calls) == 1


async def test_enrich_cwd_with_trailing_slash_matches() -> None:
    """session.cwd 多 trailing slash 也应匹配，避免假阴性。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-1",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 100,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR + "/"}]
    )
    assert out[0]["run_status"] == "running"


async def test_enrich_picks_latest_started_run_per_cwd() -> None:
    """同 cwd 下多条 run，按 startedAtUnixMs 取最晚那条的 kind（新语义）。

    旧实现按 isActive 选，会忽略真实最新的 run；改成按起始时间后，
    "最近一次跑出来什么样就是什么样"成为对外语义。
    """
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    # 故意打乱顺序：最早的 completed、最晚的 running、中间 failed。
                    {
                        "id": "r-oldest",
                        "projectDir": PROJECT_DIR,
                        "isActive": False,
                        "status": {"kind": "completed"},
                        "startedAtUnixMs": 100,
                    },
                    {
                        "id": "r-newest",
                        "projectDir": PROJECT_DIR,
                        # isActive=False 但仍应被选中（这是新逻辑相对旧逻辑的关键差异）
                        "isActive": False,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 300,
                    },
                    {
                        "id": "r-mid",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,  # 即使 isActive=True 也不会被优先选
                        "status": {"kind": "failed"},
                        "startedAtUnixMs": 200,
                    },
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    assert out[0]["run_status"] == "running"


async def test_enrich_skips_runs_without_started_at() -> None:
    """startedAtUnixMs 缺失 / 非 int 的 run 不参与 latest 计算，避免污染。

    实际 aix 输出每条 run 都带该字段，但写防御保证非法数据不会让 service 选错。
    """
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-bad",
                        "projectDir": PROJECT_DIR,
                        "isActive": False,
                        "status": {"kind": "running"},
                        # 缺 startedAtUnixMs
                    },
                    {
                        "id": "r-also-bad",
                        "projectDir": PROJECT_DIR,
                        "isActive": False,
                        "status": {"kind": "failed"},
                        "startedAtUnixMs": "not-an-int",  # 非数值
                    },
                    {
                        "id": "r-good",
                        "projectDir": PROJECT_DIR,
                        "isActive": False,
                        "status": {"kind": "completed"},
                        "startedAtUnixMs": 500,
                    },
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": PROJECT_DIR}]
    )
    # 只有 r-good 进入候选，该 cwd 的 run_status 是 completed
    assert out[0]["run_status"] == "completed"


async def test_enrich_matches_project_dir_as_child_of_cwd() -> None:
    """cwd 是 workspace root，projectDir 是其子目录 → 应匹配（前缀匹配）。

    这是线上真实场景：session.cwd = /home/admin/.aicoding/workspace/{sid}，
    而 aix run 的 projectDir = .../workspace/{sid}/project-fe。
    """
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-1",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 100,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    # cwd 是 WORKSPACE（父目录），projectDir 是 PROJECT_DIR（子目录）
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": WORKSPACE}]
    )
    assert out[0]["run_status"] == "running"


async def test_enrich_picks_latest_across_multiple_child_projects() -> None:
    """cwd 下有多个 project 子目录的 run 时，取 startedAtUnixMs 最大的。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        _CWD_AGNOSTIC,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-fe",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "completed"},
                        "startedAtUnixMs": 100,
                    },
                    {
                        "id": "r-be",
                        "projectDir": PROJECT_DIR_2,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 200,
                    },
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.enrich_with_run_status(
        [{"id": SESSION_ID, "cwd": WORKSPACE}]
    )
    # PROJECT_DIR_2 的 run 更新（startedAtUnixMs=200），所以取 running
    assert out[0]["run_status"] == "running"


# ── get_active_run_status（API 4.1 旧 per-session 路径，仍保留供回归） ──────


async def test_get_active_caches_failure() -> None:
    """Even when no active run is present, the result is cached for TTL.

    Avoids hammering subprocess for sessions that never start a devflow.
    """
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        WORKSPACE,
        BashExecResult(stdout=_runs_payload([]), stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    assert await service.get_active_run_status(SESSION_ID) == IDLE_STATUS
    cached = service._status_cache.get(SESSION_ID)
    assert cached is not None and cached[1] == IDLE_STATUS
    # TTL is positive
    assert SESSION_CACHE_TTL > 0


# ── get_session_runs ────────────────────────────────────────────────────────


async def test_get_session_runs_merges_and_sorts() -> None:
    """新命令一次返回所有 project 的 run，service 只需按 updatedAtUnixMs 排序。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        WORKSPACE,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {"id": "r-fe-old", "projectDir": PROJECT_DIR,   "updatedAtUnixMs": 50,  "isActive": False},
                    {"id": "r-fe-new", "projectDir": PROJECT_DIR,   "updatedAtUnixMs": 300, "isActive": True},
                    {"id": "r-be",     "projectDir": PROJECT_DIR_2, "updatedAtUnixMs": 200, "isActive": False},
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    runs = await service.get_session_runs(SESSION_ID)
    assert [r["id"] for r in runs] == ["r-fe-new", "r-be", "r-fe-old"]
    # 透传 projectDir，供前端按 project 分组渲染
    assert {r["id"]: r["projectDir"] for r in runs} == {
        "r-fe-new": PROJECT_DIR,
        "r-be": PROJECT_DIR_2,
        "r-fe-old": PROJECT_DIR,
    }


async def test_get_session_runs_returns_empty_when_no_aix() -> None:
    """workspace 存在但没有 .aix → aix run list exit_code != 0 → 降级 ``[]``."""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        WORKSPACE,
        BashExecResult(stdout="", stderr="no .aix directory found", exit_code=1),
    )
    service = _make_service(plugin)
    runs = await service.get_session_runs(SESSION_ID)
    assert runs == []


async def test_get_session_runs_returns_empty_on_workspace_missing() -> None:
    """``cwd`` 不存在 → BashPlugin.exec 抛 FileNotFoundError → 降级 ``[]``."""
    plugin = FakeBashPlugin()
    plugin.add_raise("aix run list", WORKSPACE, FileNotFoundError("no such dir"))
    service = _make_service(plugin)
    runs = await service.get_session_runs(SESSION_ID)
    assert runs == []


async def test_get_session_runs_returns_empty_on_bad_json() -> None:
    """命令成功但 stdout 不是合法 JSON → 降级 ``[]``，不让单点故障冒泡到 router."""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        WORKSPACE,
        BashExecResult(stdout="not json", stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    runs = await service.get_session_runs(SESSION_ID)
    assert runs == []


# ── get_run_phase_status ────────────────────────────────────────────────────


PHASE_PAYLOAD = {
    "runId": "r-xyz",
    "workflow": "spec-to-pr",
    "currentPhase": "summarize",
    "runStatus": {"kind": "completed"},
    "phases": [],
}


async def test_get_run_phase_status_success() -> None:
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(stdout=f"{PROJECT_DIR}/.aix\n", stderr="", exit_code=0),
    )
    plugin.add(
        "aix run phase status --run-id r-xyz",
        PROJECT_DIR,
        BashExecResult(stdout=json.dumps(PHASE_PAYLOAD), stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    got = await service.get_run_phase_status(SESSION_ID, "r-xyz")
    assert got["runId"] == "r-xyz"
    assert got["workflow"] == "spec-to-pr"


async def test_get_run_phase_status_rejects_command_injection() -> None:
    """run_id 含 shell 元字符必须被 400 拦在 router 之前，绝不拼到 ``bash -c``."""
    plugin = FakeBashPlugin()
    service = _make_service(plugin)
    for bad in ['r-1; rm -rf /', '$(whoami)', 'r 1', 'r\nrm', '`id`', '../etc']:
        with pytest.raises(HTTPException) as excinfo:
            await service.get_run_phase_status(SESSION_ID, bad)
        assert excinfo.value.status_code == 400
    # 一旦拒绝就不应触发任何 bash exec
    assert plugin.calls == []


async def test_get_run_phase_status_accepts_valid_run_id() -> None:
    """``r-19e59b02608-0-1673495d`` 这种格式必须放行。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(stdout=f"{PROJECT_DIR}/.aix\n", stderr="", exit_code=0),
    )
    plugin.add(
        "aix run phase status --run-id r-19e59b02608-0-1673495d",
        PROJECT_DIR,
        BashExecResult(
            stdout=json.dumps({**PHASE_PAYLOAD, "runId": "r-19e59b02608-0-1673495d"}),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.get_run_phase_status(SESSION_ID, "r-19e59b02608-0-1673495d")
    assert out["runId"] == "r-19e59b02608-0-1673495d"


async def test_get_run_phase_status_404_when_no_aix_project() -> None:
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(stdout="", stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_run_phase_status(SESSION_ID, "r-xyz")
    assert excinfo.value.status_code == 404


async def test_get_run_phase_status_404_on_not_found_stderr() -> None:
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(
            stdout=f"{PROJECT_DIR}/.aix\n{PROJECT_DIR_2}/.aix\n",
            stderr="",
            exit_code=0,
        ),
    )
    plugin.add(
        "aix run phase status --run-id r-missing",
        PROJECT_DIR,
        BashExecResult(stdout="", stderr="run not found: r-missing", exit_code=2),
    )
    plugin.add(
        "aix run phase status --run-id r-missing",
        PROJECT_DIR_2,
        BashExecResult(stdout="", stderr="run not found: r-missing", exit_code=2),
    )
    service = _make_service(plugin)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_run_phase_status(SESSION_ID, "r-missing")
    assert excinfo.value.status_code == 404


async def test_get_run_phase_status_500_on_other_error() -> None:
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(stdout=f"{PROJECT_DIR}/.aix\n", stderr="", exit_code=0),
    )
    plugin.add(
        "aix run phase status --run-id r-xyz",
        PROJECT_DIR,
        BashExecResult(stdout="", stderr="boom", exit_code=2),
    )
    service = _make_service(plugin)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_run_phase_status(SESSION_ID, "r-xyz")
    assert excinfo.value.status_code == 500
    assert "boom" in excinfo.value.detail


async def test_get_run_phase_status_500_on_invalid_json() -> None:
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(stdout=f"{PROJECT_DIR}/.aix\n", stderr="", exit_code=0),
    )
    plugin.add(
        "aix run phase status --run-id r-xyz",
        PROJECT_DIR,
        BashExecResult(stdout="not json", stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_run_phase_status(SESSION_ID, "r-xyz")
    assert excinfo.value.status_code == 500
    assert "Failed to parse aix output" in excinfo.value.detail


async def test_get_run_phase_status_tries_next_project() -> None:
    """First project errors out, second project hits → return second's payload."""
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(
            stdout=f"{PROJECT_DIR}/.aix\n{PROJECT_DIR_2}/.aix\n",
            stderr="",
            exit_code=0,
        ),
    )
    plugin.add(
        "aix run phase status --run-id r-xyz",
        PROJECT_DIR,
        BashExecResult(stdout="", stderr="run not found", exit_code=2),
    )
    plugin.add(
        "aix run phase status --run-id r-xyz",
        PROJECT_DIR_2,
        BashExecResult(stdout=json.dumps(PHASE_PAYLOAD), stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    got = await service.get_run_phase_status(SESSION_ID, "r-xyz")
    assert got["runId"] == "r-xyz"


# ── _safe_exec swallows infrastructure errors ──────────────────────────────


async def test_safe_exec_swallows_known_errors() -> None:
    plugin = FakeBashPlugin()
    plugin.add_raise("find", WORKSPACE, FileNotFoundError())
    plugin.add_raise("find", CONTAINER_WORKSPACE_BASE, ValueError("bad cwd"))
    plugin.add_raise("find", "/home/admin/", OSError("oops"))
    service = _make_service(plugin)
    # Should hit FileNotFoundError → fallback CONTAINER_WORKSPACE_BASE → ValueError
    # → "/home/admin/" → OSError; final return []
    dirs = await service._find_aix_project_dirs(WORKSPACE)
    assert dirs == []


async def test_safe_exec_swallows_timeout() -> None:
    plugin = FakeBashPlugin()
    plugin.add_raise("aix run list", WORKSPACE, asyncio.TimeoutError())
    service = _make_service(plugin)
    runs = await service._aix_run_list(WORKSPACE)
    assert runs is None


# ── enrichment with empty input ─────────────────────────────────────────────


async def test_enrich_empty_sessions_passthrough() -> None:
    service = _make_service(FakeBashPlugin())
    assert await service.enrich_with_run_status([]) == []


# ── shared-process cache (regression: instance-local cache made TTL useless) ─


async def test_cache_size_capped_to_max(monkeypatch) -> None:
    """缓存达到上限后应淘汰最旧的一半，防止 DoS / 内存累积。"""
    monkeypatch.setattr(
        "engine.community.core.aicoding.runstatus_service.SESSION_CACHE_MAX", 4
    )

    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        None,
        BashExecResult(stdout=_runs_payload([]), stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    for i in range(6):
        await service.get_active_run_status(f"sid-{i}")
    # 上限触发后保留 ≤ SESSION_CACHE_MAX 条
    assert len(service._status_cache) <= 4


async def test_cache_is_shared_across_instances() -> None:
    """Router 层每次请求新建 RunStatusService；缓存必须跨实例共享才能命中 TTL。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        WORKSPACE,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-1",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "updatedAtUnixMs": 1,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    # 第一个 service 触发真实 exec
    s1 = _make_service(plugin)
    assert await s1.get_active_run_status(SESSION_ID) == "running"
    calls_after_first = len(plugin.calls)
    # 第二个 fresh service 共享同一个 plugin；如果 cache 没共享，会再次 exec。
    s2 = _make_service(plugin)
    assert await s2.get_active_run_status(SESSION_ID) == "running"
    assert len(plugin.calls) == calls_after_first


# ── API 4.4: get_session_pull_requests ──────────────────────────────────────


def _pr_outputs_payload(outputs: list[dict]) -> str:
    return json.dumps({"outputs": outputs})


async def test_get_session_pull_requests_success_and_sort() -> None:
    """单次 aix run output list，按 at 倒序，含 runId / projectDir。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run output list",
        WORKSPACE,
        BashExecResult(
            stdout=_pr_outputs_payload(
                [
                    {
                        "runId": "r-aaa-1",
                        "kind": "pull-request",
                        "provider": "antcode",
                        "url": "https://code.example.com/x/y/pull_requests/1",
                        "title": "old PR",
                        "at": 1_000,
                        "projectDir": PROJECT_DIR,
                    },
                    {
                        "runId": "r-aaa-3",
                        "kind": "pull-request",
                        "provider": "antcode",
                        "url": "https://code.example.com/x/y/pull_requests/3",
                        "title": "newest PR",
                        "at": 3_000,
                        "projectDir": PROJECT_DIR,
                    },
                    {
                        "runId": "r-bbb-2",
                        "kind": "pull-request",
                        "provider": "antcode",
                        "url": "https://code.example.com/x/y/pull_requests/2",
                        "title": "middle PR",
                        "at": 2_000,
                        "projectDir": PROJECT_DIR_2,
                    },
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )

    service = _make_service(plugin)
    items = await service.get_session_pull_requests(SESSION_ID)

    assert [o["at"] for o in items] == [3_000, 2_000, 1_000]
    assert items[0]["title"] == "newest PR"
    assert items[0]["runId"] == "r-aaa-3"
    assert items[1]["projectDir"] == PROJECT_DIR_2


async def test_get_session_pull_requests_empty_outputs_returns_empty_list() -> None:
    """命令成功但 outputs=[] → 返回空列表，是合法状态。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run output list",
        WORKSPACE,
        BashExecResult(stdout=_pr_outputs_payload([]), stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    items = await service.get_session_pull_requests(SESSION_ID)
    assert items == []


async def test_get_session_pull_requests_500_when_cmd_fails() -> None:
    """aix run output list exit_code != 0 → 500，detail 含 stderr。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run output list",
        WORKSPACE,
        BashExecResult(stdout="", stderr="aix internal error", exit_code=1),
    )
    service = _make_service(plugin)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_session_pull_requests(SESSION_ID)
    assert excinfo.value.status_code == 500
    assert "aix run output list failed" in excinfo.value.detail
    assert "aix internal error" in excinfo.value.detail


async def test_get_session_pull_requests_500_when_safe_exec_none() -> None:
    """_safe_exec 返回 None（cwd 异常被吞）→ 500，带 'no stderr'。"""
    plugin = FakeBashPlugin()
    # 不注册任何 response → FakeBashPlugin 返回 exit_code=0 stdout=""
    # 但 _safe_exec 内部因 cwd 异常返回 None 的场景，通过 mock _safe_exec 模拟
    service = _make_service(plugin)
    # 直接模拟 _safe_exec 返回 None
    from unittest.mock import AsyncMock

    service._safe_exec = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_session_pull_requests(SESSION_ID)
    assert excinfo.value.status_code == 500
    assert "aix run output list failed" in excinfo.value.detail
    assert "no stderr" in excinfo.value.detail


async def test_get_session_pull_requests_500_on_invalid_json() -> None:
    """命令 exit_code=0 但 stdout 不是合法 JSON → 500。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run output list",
        WORKSPACE,
        BashExecResult(stdout="not a json", stderr="", exit_code=0),
    )
    service = _make_service(plugin)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_session_pull_requests(SESSION_ID)
    assert excinfo.value.status_code == 500
    assert "Failed to parse aix output" in excinfo.value.detail


async def test_get_session_pull_requests_one_success_one_fail_still_returns() -> None:
    """新方案为单次调用，不再有部分成功的场景；保留本测试以验证正常返回。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run output list",
        WORKSPACE,
        BashExecResult(
            stdout=_pr_outputs_payload(
                [
                    {
                        "runId": "r-1",
                        "kind": "pull-request",
                        "provider": "antcode",
                        "url": "https://code.example.com/x/y/pull_requests/1",
                        "title": "PR 1",
                        "at": 1_000,
                        "projectDir": PROJECT_DIR,
                    },
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    items = await service.get_session_pull_requests(SESSION_ID)
    assert len(items) == 1
    assert items[0]["url"].endswith("/1")
    assert items[0]["runId"] == "r-1"


async def test_get_session_pull_requests_missing_at_sorted_last() -> None:
    """缺 at 字段的记录用 0 兜底，排到末尾。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run output list",
        WORKSPACE,
        BashExecResult(
            stdout=_pr_outputs_payload(
                [
                    {
                        "runId": "r-no-at",
                        "kind": "pull-request",
                        "url": "https://code.example.com/x/y/pull_requests/no-at",
                        "title": "no-at PR",
                        "projectDir": PROJECT_DIR,
                    },
                    {
                        "runId": "r-with-at",
                        "kind": "pull-request",
                        "url": "https://code.example.com/x/y/pull_requests/with-at",
                        "title": "with-at PR",
                        "at": 5_000,
                        "projectDir": PROJECT_DIR,
                    },
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    items = await service.get_session_pull_requests(SESSION_ID)
    assert items[0]["title"] == "with-at PR"
    assert items[1]["title"] == "no-at PR"


# ── _aix_run_list_all cwd 白名单回归 ──────────────────────────────────


async def test_aix_run_list_all_uses_workspace_base_as_cwd(monkeypatch) -> None:
    """_aix_run_list_all 的 cwd 必须是 _resolve_workspace_base() 的返回值，
    确保能通过 BashService 白名单（前缀 /home/admin/）。

    回归：历史版本直接用 os.environ.get("HOME")="/home/admin"（无尾部斜杠），
    被白名单拦截导致所有 session 静默降级为 idle。
    """
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        CONTAINER_WORKSPACE_BASE,
        BashExecResult(
            stdout=_runs_payload(
                [
                    {
                        "id": "r-1",
                        "projectDir": PROJECT_DIR,
                        "isActive": True,
                        "status": {"kind": "running"},
                        "startedAtUnixMs": 100,
                    }
                ]
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    result = await service._aix_run_list_all()
    assert result is not None
    assert len(result) == 1
    # 验证实际传递的 cwd
    assert len(plugin.calls) == 1
    actual_cwd = plugin.calls[0][1]
    assert actual_cwd == CONTAINER_WORKSPACE_BASE


async def test_aix_run_list_all_respects_relay_cwd_env(monkeypatch) -> None:
    """RELAY_DEFAULT_CWD 环境变量优先于硬编码 CONTAINER_WORKSPACE_BASE。"""
    custom_cwd = "/workspace/custom-relay"
    monkeypatch.setenv("RELAY_DEFAULT_CWD", custom_cwd)

    plugin = FakeBashPlugin()
    plugin.add(
        "aix run list",
        custom_cwd,
        BashExecResult(
            stdout=_runs_payload([]),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    result = await service._aix_run_list_all()
    assert result == []
    assert plugin.calls[0][1] == custom_cwd


# ── 边角分支补漏（runstatus_service 100% line coverage） ───────────────


async def test_get_run_phase_status_skips_safe_exec_none() -> None:
    """phase 第一个 candidate 的 _safe_exec 返回 None（被吞的 cwd 异常），
    应继续尝试下一个 candidate；命中后正常返回。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(
            stdout=f"{PROJECT_DIR}/.aix\n{PROJECT_DIR_2}/.aix\n",
            stderr="",
            exit_code=0,
        ),
    )
    # 第一个 candidate 跑命令时 plugin 抛 FileNotFoundError → _safe_exec 返回 None
    plugin.add_raise(
        "aix run phase status",
        PROJECT_DIR,
        FileNotFoundError("cwd missing"),
    )
    plugin.add(
        "aix run phase status",
        PROJECT_DIR_2,
        BashExecResult(
            stdout=json.dumps({**PHASE_PAYLOAD, "runId": "r-skip-none"}),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    out = await service.get_run_phase_status(SESSION_ID, "r-skip-none")
    assert out["runId"] == "r-skip-none"


async def test_get_session_pull_requests_skips_non_dict_outputs() -> None:
    """outputs 中混入非 dict 条目（如 null）时应被过滤掉。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "aix run output list",
        WORKSPACE,
        BashExecResult(
            stdout=json.dumps(
                {
                    "outputs": [
                        {
                            "runId": "r-ok",
                            "kind": "pull-request",
                            "url": "https://code.example.com/x/y/pull_requests/9",
                            "title": "only PR",
                            "at": 9_000,
                            "projectDir": PROJECT_DIR,
                        },
                        None,
                        "not a dict",
                    ]
                }
            ),
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    items = await service.get_session_pull_requests(SESSION_ID)
    assert len(items) == 1
    assert items[0]["title"] == "only PR"


async def test_find_aix_project_dirs_skips_empty_lines() -> None:
    """find 输出含空行/空白行时应被 strip 后跳过，不污染结果。"""
    plugin = FakeBashPlugin()
    plugin.add(
        "find",
        WORKSPACE,
        BashExecResult(
            # 头尾、中间各塞空行 / 仅空格行
            stdout=f"\n{PROJECT_DIR}/.aix\n   \n{PROJECT_DIR_2}/.aix\n\n",
            stderr="",
            exit_code=0,
        ),
    )
    service = _make_service(plugin)
    dirs = await service._find_aix_project_dirs(WORKSPACE)
    assert dirs == [PROJECT_DIR, PROJECT_DIR_2]


async def test_safe_exec_logs_warning_on_unexpected_exception(caplog) -> None:
    """plugin 抛"未知异常"（非已知清单）时，_safe_exec 应返回 None 且打 warning。"""
    plugin = FakeBashPlugin()
    plugin.add_raise("aix run list", WORKSPACE, RuntimeError("surprise"))
    service = _make_service(plugin)

    import logging

    with caplog.at_level(logging.WARNING, logger="aicoding-runstatus"):
        result = await service._aix_run_list(WORKSPACE)

    assert result is None
    assert any("bash exec unexpected error" in rec.message for rec in caplog.records)
    assert any("surprise" in rec.message for rec in caplog.records)
