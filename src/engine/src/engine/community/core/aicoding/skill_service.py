"""SkillListService — aix skill list --json for AICoding containers.

直接消费容器内本机 BashService（asyncio.subprocess），不需要任何 Bolt / HTTP 跳转。
与 RunStatusService 同套依赖注入风格：router 每次 fresh 构造一个 service 实例，
service 通过 BashService 在容器内执行 ``aix skill list --json`` 并透传输出。

service 不重塑结构——``aix`` 原始 ``{backends: {...}}`` dict 原样返回，
结构规整（每个 backend 节点只保留 ``skills``、丢弃杂字段）在 schema 层完成，
保持与 RunStatusService 的透传语义一致。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException

from engine.community.core.aicoding.workspace_service import _resolve_workspace_base

if TYPE_CHECKING:
    from engine.community.core.bash.models import BashExecResult
    from engine.community.core.bash.protocol import BashService

log = logging.getLogger("aicoding-skills")

SKILL_LIST_TIMEOUT = 10  # aix skill list --json


class SkillListService:
    """通过 BashService 在容器内本机执行 ``aix skill list --json``。

    设计要点：
    1. ``aix skill list`` 是**容器级**资源（与具体 session 无关），因此
       不需要 ``session_id`` 参数，cwd 用 ``_resolve_workspace_base()`` 兜底
       （落在 ``/home/admin/`` 白名单内，恒定存在）。
    2. exec 异常（cwd / 子进程启动失败 / 超时）在 service 内吞掉返回 None，
       再统一收敛为 500；与 RunStatusService 的 ``_safe_exec`` 同款兜底。
    3. ``exit_code != 0`` / JSON 解析失败 → 抛 HTTPException(500)，带 stderr 或
       解析错误信息；无 404 分支（非 session 维度，不存在 workspace 不存在语义）。
    """

    def __init__(self, bash_plugin: "BashService") -> None:
        self._bash = bash_plugin

    # ── public ────────────────────────────────────────────────────────────────

    async def list_skills(self) -> dict:
        """执行 ``aix skill list --json``，返回 aix 原始 ``{backends: {...}}`` dict。

        错误语义：

        - 命令执行失败 / exit_code != 0 → 抛 500，带 stderr；
        - JSON 解析失败 → 抛 500。
        """
        cmd = "aix skill list --json"
        exec_cwd = _resolve_workspace_base()
        res = await self._safe_exec(cmd, exec_cwd, SKILL_LIST_TIMEOUT)

        if res is None or res.exit_code != 0:
            stderr = res.stderr if res else "no stderr"
            raise HTTPException(
                status_code=500,
                detail=f"aix skill list failed: {stderr}",
            )

        try:
            payload = json.loads(res.stdout) or {}
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse aix output: {e}",
            ) from e

        return payload

    # ── internal ──────────────────────────────────────────────────────────────

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


__all__ = ["SkillListService", "SKILL_LIST_TIMEOUT"]
