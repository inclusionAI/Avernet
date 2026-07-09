"""RunStatusService — aix run list / aix run phase status enrichment for AICoding sessions.

直接消费容器内本机 BashService（asyncio.subprocess），不需要任何 Bolt / HTTP 跳转。
与 WorkspaceService 同套依赖注入风格。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException

from engine.community.core.aicoding.workspace_service import (
    CONTAINER_WORKSPACE_BASE,
    WorkspaceService,
    _resolve_workspace_base,
)

if TYPE_CHECKING:
    from engine.community.core.bash.models import BashExecResult
    from engine.community.core.bash.protocol import BashService

log = logging.getLogger("aicoding-runstatus")

RUNS_TIMEOUT = 8         # 单次 aix run list（带或不带 --filter）
PHASE_TIMEOUT = 20       # aix run phase status --verbose
OUTPUT_LIST_TIMEOUT = 8  # aix runs output list --kind pull-request --json
FIND_TIMEOUT = 5         # find -name .aix
SESSION_CACHE_TTL = 30    # session 维度缓存（成功/失败均缓存）
SESSION_CACHE_MAX = 1024  # 进程内缓存最多保留多少条 session（防止 DoS / 内存累积）

# aix run list --json 中 status.kind 原样透传给前端，service 不再做语义合并/重命名。
# 已知 aix kind 包括（但不限于）：
#   running / awaitingLlmEvaluation / awaitingApproval / awaitingHuman /
#   completed / failed
# 任何"没拿到活跃 run"的情况（没有 .aix / 没有 isActive run / aix 调用失败 /
# enrichment 总超时）一律收敛为 ``idle``——前端不需要处理 null 分支。代价是
# 丢失"调用失败 vs 真未启动"的区分；调用失败会有 log warning 兜底排查。
IDLE_STATUS = "idle"


def _normalize_status_kind(kind: Optional[str]) -> str:
    """无活跃 run 时返回 ``idle``，其余原样透传 aix ``status.kind``。"""
    return IDLE_STATUS if kind is None else kind


def _norm_path(p: Optional[str]) -> str:
    """规范化路径用于 cwd ↔ projectDir 字符串匹配。

    aix runs 的 ``projectDir`` 与 relay 回传的 ``session.cwd`` 都形如
    ``/home/admin/.aicoding/workspace/<uuid>``，但来源不同，可能差一个 trailing
    slash 或 ``./`` 段。统一过一遍 ``normpath + rstrip("/")``，避免假阴性匹配。
    ``None`` / 空串归一为 ``""``，配合 ``dict.get(key, IDLE_STATUS)`` 直接走兜底。
    """
    if not p:
        return ""
    return os.path.normpath(p).rstrip("/")


# 进程内共享缓存：session_id -> (monotonic_ts, run_status)。
# 路由层每次请求都会通过 ``RunStatusService(bash_plugin=manager.bash)`` 新建一个
# service 实例（与 ``_workspace_service()`` 同款），如果把 cache 放在 instance 上,
# 30s TTL 永远命中不到——必须把 cache 提到模块级，让所有 instance 共享。
# 缓存值为 aix 原生 ``status.kind`` 字符串（或 ``idle``）。
_PROCESS_STATUS_CACHE: dict[str, tuple[float, str]] = {}

# aix run id 由 aix CLI 生成，形如 ``r-19e59b02608-0-1673495d``；
# 只允许字母 / 数字 / ``-`` / ``_``，避免 f-string 拼到 ``bash -c`` 时被命令注入。
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _clear_status_cache() -> None:
    """主要供测试调用，避免跨用例污染共享缓存。"""
    _PROCESS_STATUS_CACHE.clear()


class RunStatusService:
    """通过 BashService 在容器内本机执行 aix CLI。

    设计要点：
    1. **API 4.1 一次 aix 调用**：:meth:`enrich_with_run_status` 跑一次裸
       ``aix run list --json``，由 aix 自己聚合容器内所有 workspace 的 run；
       service 按 ``runs[].projectDir`` 分组、取 ``startedAtUnixMs`` 最晚的一条
       run 的 ``status.kind`` 作为该 session 的 ``run_status``。相比"每个 session
       单跑一次 ``--filter <cwd>``"少 N-1 次 subprocess。
    2. **API 4.2 仍按 session 单跑**：:meth:`get_session_runs` 透传指定 session
       的全部 run 给前端，用 ``aix run list --filter <workspace_root>`` 走
       per-session 路径，避免响应体里塞别的 session 的 run。
    3. 所有 bash exec 异常在 service 内吞掉、降级返回 None / [], 绝不让单点失败冒泡到 router。
    4. ``aix run phase status`` / ``aix runs output list`` 仍按"可能多个 ``.aix/``"工作，
       所以保留 :meth:`_find_aix_project_dirs`；它先 ``find -maxdepth 2 -name .aix``
       定位 session 自己的 ``.aix/``（当前部署形态下整个 session 工作空间共享**唯一一个**
       ``.aix/``，位于 session 根 ``/home/admin/.aicoding/workspace/{session_id}/.aix``），
       再在其父目录上跑对应命令。
    5. ``_status_cache`` 引用模块级 ``_PROCESS_STATUS_CACHE``，跨请求复用 30s TTL；
       目前仅 :meth:`get_active_run_status` 使用（4.2 ``get_session_runs`` 不查缓存）。
    """

    def __init__(self, bash_plugin: "BashService") -> None:
        self._bash = bash_plugin
        # 引用模块级共享缓存；service 实例 fresh 不影响 TTL。
        self._status_cache: dict[str, tuple[float, str]] = (
            _PROCESS_STATUS_CACHE
        )

    # ── public ────────────────────────────────────────────────────────────────

    async def enrich_with_run_status(self, sessions: list[dict]) -> list[dict]:
        """API 4.1：对 sessions 列表追加 run_status 字段。

        实现：一次 ``aix run list --json``（不带 ``--filter``）拿到容器内**所有**
        workspace 的 run，按 ``projectDir`` 分组取 ``startedAtUnixMs`` 最晚的一条
        run 的 ``status.kind``，再用 session 的 ``cwd`` 字段去匹配。比"每个 session
        单跑一次 ``--filter <cwd>``"少 N-1 次 subprocess，N=20 的列表请求差距显著。

        历史上曾按 ``isActive`` 字段选活跃 run，但实测同一 cwd 下可能存在
        ``status.kind=running`` 但 ``isActive=false`` 的记录（aix 状态机内部状态），
        会让前端漏掉真实进行中的 run。改成"按起始时间取最新"后语义更接近用户直觉：
        会话最新一次跑出来的状态就是它的当前状态。

        ``run_status`` 原样透传 aix ``status.kind``（如 ``running`` /
        ``awaitingApproval`` / ``awaitingHuman`` / ``awaitingLlmEvaluation`` /
        ``completed`` / ``failed``）；该 cwd 在 aix runs 里完全没出现 / aix 调用
        失败 / session 缺 cwd 一律为 ``idle``。
        """
        if not sessions:
            return sessions

        all_runs = await self._aix_run_list_all()
        # cwd → (startedAtUnixMs, status.kind) 的 best-so-far：
        # 同一 cwd 多条 run 时取 startedAtUnixMs 最大的那条 kind 作为输出。
        latest_by_cwd: dict[str, tuple[int, str]] = {}
        for r in all_runs or []:
            cwd_key = _norm_path(r.get("projectDir"))
            if not cwd_key:
                continue
            kind = (r.get("status") or {}).get("kind")
            if not kind:
                continue
            started = r.get("startedAtUnixMs")
            if not isinstance(started, int):
                continue
            prev = latest_by_cwd.get(cwd_key)
            if prev is None or started > prev[0]:
                latest_by_cwd[cwd_key] = (started, kind)

        for s in sessions:
            cwd_key = _norm_path(s.get("cwd"))
            if not cwd_key:
                s["run_status"] = IDLE_STATUS
                continue
            # cwd 是 projectDir 的父目录或相同目录 → 属于该 session
            best: tuple[int, str] | None = None
            for proj_path, (started, kind) in latest_by_cwd.items():
                if proj_path == cwd_key or proj_path.startswith(cwd_key + "/"):
                    if best is None or started > best[0]:
                        best = (started, kind)
            s["run_status"] = best[1] if best else IDLE_STATUS
        return sessions

    async def get_active_run_status(self, session_id: str) -> str:
        """返回该 session 当前活跃 run 的 run_status。带 30s 进程内缓存。

        值为 aix 原生 ``status.kind`` 字符串；未启动 / 取不到活跃 run / 调用失败
        一律为 ``idle``。缓存的也是同一份字符串。
        """
        now = time.monotonic()
        cached = self._status_cache.get(session_id)
        if cached and now - cached[0] < SESSION_CACHE_TTL:
            return cached[1]

        runs = await self._collect_runs_for_session(session_id)
        raw_kind: Optional[str] = None
        if runs:
            for r in runs:
                if r.get("isActive"):
                    raw_kind = (r.get("status") or {}).get("kind")
                    break
        active = _normalize_status_kind(raw_kind)
        # 在写入前做一次 size 兜底：丢弃最旧的一半，防止无限堆积。
        if len(self._status_cache) >= SESSION_CACHE_MAX:
            oldest = sorted(
                self._status_cache.items(), key=lambda kv: kv[1][0]
            )
            for old_key, _ in oldest[: SESSION_CACHE_MAX // 2]:
                self._status_cache.pop(old_key, None)
        self._status_cache[session_id] = (now, active)
        return active

    async def get_session_runs(self, session_id: str) -> list[dict]:
        """API 4.2：返回该 session 工作空间下的所有 runs，倒序。

        若 session workspace 不存在，抛 ``FileNotFoundError`` 让 router
        层转 404；这与 API 4.1（列表+enrich）的"静默兜底为 idle"区分开。
        """
        WorkspaceService.ensure_workspace_exists(session_id)
        runs = await self._collect_runs_for_session(session_id)
        return runs or []

    async def get_run_phase_status(self, session_id: str, run_id: str) -> dict:
        """API 4.3：在 session 工作空间的 project 子目录中查找该 run_id 的 phase 详情。"""
        if not _RUN_ID_RE.match(run_id):
            raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}")

        cwd = WorkspaceService.ensure_workspace_exists(session_id)
        candidates = await self._find_aix_project_dirs(cwd)
        if not candidates:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        not_found_seen = False
        last_stderr = ""
        cmd = f"aix run phase status --run-id {run_id} --json --verbose"
        for project_dir in candidates:
            res = await self._safe_exec(cmd, project_dir, PHASE_TIMEOUT)
            if res is None:
                continue  # bash exec 异常，跳到下一个 candidate
            if res.exit_code == 0:
                try:
                    return json.loads(res.stdout)
                except json.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to parse aix output: {e}",
                    ) from e
            stderr_lower = (res.stderr or "").lower()
            if "not found" in stderr_lower or "unknown run" in stderr_lower:
                not_found_seen = True
            last_stderr = res.stderr or last_stderr

        if not_found_seen:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        raise HTTPException(
            status_code=500,
            detail=f"aix run phase status failed: {last_stderr or 'no stderr'}",
        )

    async def get_session_pull_requests(self, session_id: str) -> list[dict]:
        """API 4.4：返回 session 工作空间下所有 run 产出的 pull-request outputs，按 at 倒序。

        执行 ``aix run output list --kind pull-request --json --filter <workspace_root>``，
        由 aix 自己向下递归找 ``.aix/``，service 不再需要预先 find + 多目录串行调用。

        错误语义：

        - workspace 不存在 → ``FileNotFoundError``，router 转 404；
        - 命令执行失败 / exit_code != 0 → 抛 500，带 stderr；
        - JSON 解析失败 → 抛 500。
        """
        workspace_root = WorkspaceService.resolve_workspace(session_id)
        return await self._aix_run_output_list(workspace_root)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _collect_runs_for_session(self, session_id: str) -> Optional[list[dict]]:
        """在 session workspace root 上执行 ``aix run list --filter <root> --json``，
        由 aix 自己向下递归找 ``.aix/`` 并返回所有 run（含 ``projectDir``）。

        新命令一次返回所有 run，因此不再需要旧实现的 ``find -name .aix`` 预扫 +
        多 project 串行调用 + 合并步骤。排序仍按 ``updatedAtUnixMs`` 兜底，因为
        aix 返回顺序未约定。
        """
        workspace_root = WorkspaceService.resolve_workspace(session_id)
        runs = await self._aix_run_list(workspace_root)
        if not runs:
            return None
        runs.sort(key=lambda r: r.get("updatedAtUnixMs") or 0, reverse=True)
        return runs

    async def _find_aix_project_dirs(self, root: str) -> list[str]:
        """find -maxdepth 2 -name .aix -type d；返回 .aix 的父目录列表。

        当前部署形态下 ``.aix/`` 位于 session 根（depth 1），所以返回的列表
        实际只有 1 条 = ``root`` 本身；保留 -maxdepth 2 是为了兼容未来可能
        拆到 project 级（depth 2）的形态。

        cwd 不存在 / find 退出非 0 / 子进程启动失败 → 统一返回 []。
        """
        cmd = f'find "{root}" -maxdepth 2 -name .aix -type d 2>/dev/null'
        # cwd 不存在时，BashService 子进程会启动失败；用 CONTAINER_WORKSPACE_BASE 兜底。
        fallback_cwd = (
            root
            if root.startswith(CONTAINER_WORKSPACE_BASE)
            else CONTAINER_WORKSPACE_BASE
        )
        res = await self._safe_exec(cmd, fallback_cwd, FIND_TIMEOUT)
        if res is None:
            # cwd 都不存在的极端情况，再退一档到容器根。
            res = await self._safe_exec(cmd, "/home/admin/", FIND_TIMEOUT)
        if res is None or res.exit_code != 0:
            return []
        out: list[str] = []
        for line in (res.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            project_dir = line.rsplit("/.aix", 1)[0]
            if project_dir:
                out.append(project_dir)
        return out

    async def _aix_run_list(self, workspace_root: str) -> Optional[list[dict]]:
        """单次 ``aix run list --filter <workspace_root> --json``；任何异常或非 0 退出 → None。

        ``--filter`` 让 aix 自己向下递归找 ``.aix/``，service 不再需要预先 find；
        ``workspace_root`` 用 ``shlex.quote`` 转义，session_id 可能含 ``:`` 等
        在 bash 中需要被引号包住的字符。cwd 用 workspace_root 本身，已落在
        ``/home/admin/`` 白名单内；workspace 不存在时 ``_safe_exec`` 会吞
        ``FileNotFoundError`` 返回 None。

        当前仅 :meth:`get_session_runs`（API 4.2）走这条 per-session 路径；
        :meth:`enrich_with_run_status`（API 4.1）已切到 :meth:`_aix_run_list_all`。
        """
        cmd = f"aix run list --filter {shlex.quote(workspace_root)} --json"
        res = await self._safe_exec(cmd, workspace_root, RUNS_TIMEOUT)
        if res is None or res.exit_code != 0:
            return None
        try:
            return (json.loads(res.stdout) or {}).get("runs") or []
        except json.JSONDecodeError:
            return None

    async def _aix_run_list_all(self) -> Optional[list[dict]]:
        """裸跑 ``aix run list --json``（不带 ``--filter``）拿容器内所有 workspace 的 run。

        aix 自己已经按 workspace 聚合并在 runs[].projectDir 上回填路径，所以
        service 一次 subprocess 就能拿到全部数据，供 :meth:`enrich_with_run_status`
        按 cwd 索引。cwd 用 ``CONTAINER_WORKSPACE_BASE``（恒定存在 + 落在
        ``/home/admin/`` 白名单内）；任何异常或非 0 退出 → None，调用方按"无活跃
        run → idle"兜底。

        当前部署形态下一个 ARCA sandbox 容器即一个用户，所以这里"所有 workspace
        的 run"在用户维度等价于"该用户所有 session 的 run"，不存在跨用户越权。
        """
        cmd = "aix run list --json"
        exec_cwd = _resolve_workspace_base()
        res = await self._safe_exec(cmd, exec_cwd, RUNS_TIMEOUT)
        if res is None or res.exit_code != 0:
            return None
        try:
            return (json.loads(res.stdout) or {}).get("runs") or []
        except json.JSONDecodeError:
            return None

    async def _aix_run_output_list(self, workspace_root: str) -> list[dict]:
        """单次 ``aix run output list --kind pull-request --json --filter <workspace_root>``。

        ``--filter`` 让 aix 自己向下递归找 ``.aix/``，service 不再需要预先 find +
        多目录串行调用 + 合并步骤。失败时抛 HTTPException。

        错误语义：

        - 命令执行失败 / exit_code != 0 → 500，带 stderr；
        - JSON 解析失败 → 500。
        """
        cmd = (
            f"aix run output list --kind pull-request --json"
            f" --filter {shlex.quote(workspace_root)}"
        )
        res = await self._safe_exec(cmd, workspace_root, OUTPUT_LIST_TIMEOUT)

        if res is None or res.exit_code != 0:
            stderr = res.stderr if res else "no stderr"
            raise HTTPException(
                status_code=500,
                detail=f"aix run output list failed: {stderr}",
            )

        try:
            payload = json.loads(res.stdout) or {}
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse aix output: {e}",
            ) from e

        outputs = payload.get("outputs") or []
        if not isinstance(outputs, list):
            outputs = []

        # 先过滤非 dict 条目，再按 at 倒序；缺失/非数值的记录用 0 兜底排到末尾
        outputs = [o for o in outputs if isinstance(o, dict)]
        outputs.sort(
            key=lambda o: o.get("at") if isinstance(o.get("at"), int) else 0,
            reverse=True,
        )
        return outputs

    async def _safe_exec(
        self, cmd: str, cwd: str, timeout: int
    ) -> Optional["BashExecResult"]:
        """BashService.exec 的兜底封装：所有异常返回 None，绝不冒泡。

        BashService.exec 可能因 cwd 不存在抛 FileNotFoundError，因 cwd 不在白名单
        抛 ValueError，因子进程启动失败抛 OSError；统一吞掉。
        """
        try:
            return await self._bash.exec(cmd=cmd, cwd=cwd, timeout=timeout)
        except (FileNotFoundError, ValueError, OSError, asyncio.TimeoutError):
            return None
        except Exception as e:
            log.warning(
                "bash exec unexpected error: cmd=%r cwd=%r err=%s", cmd, cwd, e
            )
            return None


__all__ = [
    "RunStatusService",
    "_clear_status_cache",
    "_normalize_status_kind",
    "_norm_path",
    "IDLE_STATUS",
]
