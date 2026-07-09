"""``/api/aicoding/skills`` router (api 层).

返回当前 AICoding 容器生效的全部 skill，按 backend 分组
（透传 ``aix skill list --json``）。与 ``aicoding_sessions`` 下的 aix 透传
接口（4.2 / 4.3 / 4.4）同一条执行链路：router → ``SkillListService`` →
容器内本机 ``BashService``，无 Bolt / HTTP 跳转。

与 session 维度接口的区别：``aix skill list`` 是**容器级**资源（与具体
session 无关，skill 路径形如 ``/home/admin/.claude/...``），因此本接口
不带 ``session_id`` 参数，也无 404 分支。

结构规整（每个 backend 节点只保留 ``skills``、丢弃杂字段）在 schema 层完成，
service 原样透传 aix 输出。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from engine.community.api.aicoding.skill_schemas import (
    BackendSkillsSchema,
    SkillInfoSchema,
    SkillListResponse,
)
from engine.community.api.caps import check_capability
from engine.community.core.aicoding.skill_service import SkillListService
from engine.community.core.engine.capability import Capability

log = logging.getLogger("api-aicoding-skills")

router = APIRouter(prefix="/api/aicoding", tags=["aicoding-skills"])


# ── plugin accessors ─────────────────────────────────────────────────


def _skill_service() -> SkillListService:
    """Build a SkillListService over the active engine's BashService.

    Mirrors ``aicoding_sessions.router._runstatus_service`` — no DI registration,
    just a lightweight wrapper that runs ``aix`` CLI inside the same container
    via the local subprocess BashService.
    """
    from engine.community.manager import EngineManager

    manager = EngineManager.get_instance()
    return SkillListService(bash_plugin=manager.bash)


# ── routes ───────────────────────────────────────────────────────────


@router.get("/skills", response_model=SkillListResponse)
async def list_skills() -> SkillListResponse:
    """返回当前容器生效的全部 skill，按 backend 分组（透传 ``aix skill list --json``）。

    错误语义：

    - 命令执行失败 / ``exit_code != 0`` → 500（带 stderr）；
    - JSON 解析失败 → 500。
    """
    check_capability(Capability.BASH_EXEC)
    service = _skill_service()
    raw = await service.list_skills()

    backends_raw = raw.get("backends") or {}
    backends: dict[str, BackendSkillsSchema] = {}
    for name, value in backends_raw.items():
        if not isinstance(value, dict):
            continue
        skills_raw = value.get("skills") or []
        backends[name] = BackendSkillsSchema(
            skills=[
                SkillInfoSchema(**s)
                for s in skills_raw
                if isinstance(s, dict) and s.get("name")
            ]
        )
    return SkillListResponse(success=True, backends=backends)
