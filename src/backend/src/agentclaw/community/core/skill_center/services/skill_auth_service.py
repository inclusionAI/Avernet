"""SkillAuthService - 按 Skill / SkillSet 维度鉴权 & 批量申请

Migrated from: services/openclawserver/server/services/skill_auth_service.py

将 Skill 的 mcp_dependencies 展开，对每个 MCP server 进行鉴权/申请。
支持 SkillSet 级别：汇总所有 Skill 的 MCP 依赖，去重后统一鉴权/申请。
"""

from typing import Any

from injector import inject

from agentclaw.community.core.mcp.services.auth_service import MCPAuthService
from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin


logger = get_logger()


class SkillAuthService:
    """Skill 维度的鉴权与权限申请服务。

    复用 MCPCenterPlugin.check_mcp_permission_detail 做单 MCP 鉴权,
    复用 MCPAuthService.apply_permission 做单 MCP 申请。
    """

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        mcp_center: MCPCenterPlugin,
        mcp_auth_service: MCPAuthService,
    ):
        self.skill_repo = skill_repo
        self.skill_set_repo = skill_set_repo
        self.mcp_center = mcp_center
        self.mcp_auth_service = mcp_auth_service

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    @staticmethod
    def _extract_server_codes(skill: dict) -> list[str]:
        """从单个 skill dict 的 mcp_dependencies 提取去重的 server_code 列表。"""
        deps = skill.get("mcp_dependencies") or []
        seen = set()
        codes = []
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            code = dep.get("code") or dep.get("name") or ""
            code = code.strip()
            if code and code not in seen:
                seen.add(code)
                codes.append(code)
        return codes

    def _get_mcp_server_codes(self, skill_id: str) -> tuple[dict, list[str]]:
        """从 skill 的 mcp_dependencies 提取去重后的 server_code 列表。

        Returns:
            (skill_dict, server_codes)

        Raises:
            ValueError: skill 不存在
        """
        skill = self.skill_repo.get_by_id(skill_id)
        if not skill:
            raise ValueError(f"Skill not found: {skill_id}")
        return skill, self._extract_server_codes(skill)

    # ----------------------------------------------------------
    # 公开方法
    # ----------------------------------------------------------

    def check_skill_permission(self, user_id: str, skill_id: str) -> dict[str, Any]:
        """检查用户对 Skill 所有 MCP 依赖的权限状态。

        Returns:
            {
                "skill_id": str,
                "skill_name": str,
                "authorized": bool,          # 所有 MCP 都有权限时为 True
                "mcp_details": {
                    "<server_code>": {
                        "has_permission": bool,
                        "access_level": str,
                        "tool_permissions": { ... },
                    }
                }
            }
        """
        skill, server_codes = self._get_mcp_server_codes(skill_id)
        skill_name = skill.get("name", "")

        if not server_codes:
            return {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "authorized": True,
                "mcp_details": {},
            }

        mcp_details: dict[str, Any] = {}
        all_authorized = True

        for code in server_codes:
            try:
                detail = self.mcp_auth_service.check_mcp_permission_detail(user_id, code)
                mcp_details[code] = detail
                if not detail.get("has_permission"):
                    all_authorized = False
            except Exception:
                logger.exception("[SkillAuth] check failed for server_code=%s", code)
                mcp_details[code] = {
                    "has_permission": False,
                    "access_level": "",
                    "tool_permissions": {},
                    "error": f"查询失败: {code}",
                }
                all_authorized = False

        return {
            "skill_id": skill_id,
            "skill_name": skill_name,
            "authorized": all_authorized,
            "mcp_details": mcp_details,
        }

    def apply_skill_permission(
        self,
        user_id: str,
        skill_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """为用户批量申请 Skill 所有 MCP 依赖的权限。

        Returns:
            {
                "skill_id": str,
                "skill_name": str,
                "all_authorized": bool,
                "apply_results": {
                    "<server_code>": {
                        "success": bool,
                        "process_url": str | None,
                        "error": str | None,
                        "already_authorized": bool,
                    }
                }
            }
        """

        skill, server_codes = self._get_mcp_server_codes(skill_id)
        skill_name = skill.get("name", "")

        if not server_codes:
            return {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "all_authorized": True,
                "apply_results": {},
            }

        auth_svc = self.mcp_auth_service
        apply_results: dict[str, Any] = {}
        all_ok = True

        for code in server_codes:
            try:
                # 获取 MCP 详情以确定 is_public 和 tool_list
                mcp_data = self.mcp_center.get_mcp_detail(code)
                if not mcp_data:
                    apply_results[code] = {
                        "success": False,
                        "process_url": None,
                        "error": f"MCP server not found: {code}",
                        "already_authorized": False,
                    }
                    all_ok = False
                    continue

                access_level = mcp_data.get("accessLevel", "")
                is_public = access_level == "PUBLIC"
                tools = mcp_data.get("tools", [])
                tool_names = [t["name"] for t in tools if isinstance(t, dict) and t.get("name")]

                result = auth_svc.apply_permission(
                    staff_no=user_id,
                    service_code=code,
                    tool_list=tool_names,
                    is_public=is_public,
                    reason=reason,
                )

                already_authorized = (
                    result.get("success") and result.get("process_url") is None
                )
                apply_results[code] = {
                    "success": result.get("success", False),
                    "process_url": result.get("process_url"),
                    "error": result.get("error"),
                    "already_authorized": already_authorized,
                }
                if not result.get("success"):
                    all_ok = False

            except Exception:
                logger.exception("[SkillAuth] apply failed for server_code=%s", code)
                apply_results[code] = {
                    "success": False,
                    "process_url": None,
                    "error": f"申请失败: {code}",
                    "already_authorized": False,
                }
                all_ok = False

        return {
            "skill_id": skill_id,
            "skill_name": skill_name,
            "all_authorized": all_ok,
            "apply_results": apply_results,
        }

    # ----------------------------------------------------------
    # Bot 级别
    # ----------------------------------------------------------

    def _collect_bot_mcp_codes(
        self, bot_id: str
    ) -> tuple[list[dict], list[str]]:
        """获取 bot 下所有 skill_set 并汇总去重的 MCP server_code。

        MCP 来源两条路径:
        1. ac_skill_set_mcp — skill_set 直接关联的 MCP
        2. ac_skill_set_skill → ac_skill.mcp_dependencies — 技能依赖的 MCP

        Args:
            bot_id: Bot 标识 (对应 ac_skill_set.bolt_id)

        Returns:
            (skill_sets, all_server_codes)
            - skill_sets: bot 关联的 skill_set 列表
            - all_server_codes: 去重后的全局 server_code 列表

        Raises:
            ValueError: 找不到任何 skill_set
        """
        skill_sets = self.skill_set_repo.list_all(bolt_id=bot_id)
        if not skill_sets:
            raise ValueError(f"No skill_set found for bot: {bot_id}")

        seen: set[str] = set()
        all_codes: list[str] = []

        for ss in skill_sets:
            ss_id = str(ss.get("id", ""))

            # 路径 1: skill_set 直接关联的 MCP
            mcp_servers = self.skill_set_repo.get_mcp_servers_in_set(ss_id)
            for mcp in mcp_servers:
                code = (mcp.get("server_code") or "").strip()
                if code and code not in seen:
                    seen.add(code)
                    all_codes.append(code)

            # 路径 2: skill_set 内 skill 的 mcp_dependencies
            skills = self.skill_set_repo.get_skills_in_set(ss_id)
            for sk in (skills or []):
                for code in self._extract_server_codes(sk):
                    if code not in seen:
                        seen.add(code)
                        all_codes.append(code)

        return skill_sets, all_codes

    def check_bot_permission(self, user_id: str, bot_id: str) -> dict[str, Any]:
        """查询用户对 Bot 所有 MCP 依赖的权限状态。

        Args:
            user_id: 用户工号
            bot_id: Bot 标识 (对应 ac_skill_set.bolt_id)

        Returns:
            {
                "bot_id": str,
                "authorized": bool,          # 所有 MCP 都有权限时为 True
                "mcp_details": {
                    "<server_code>": {
                        "has_permission": bool,
                        "access_level": str,
                        "tool_permissions": { ... },
                    }
                }
            }
        """
        _skill_sets, server_codes = self._collect_bot_mcp_codes(bot_id)

        if not server_codes:
            return {
                "bot_id": bot_id,
                "authorized": True,
                "mcp_details": {},
            }

        mcp_details: dict[str, Any] = {}
        all_authorized = True

        for code in server_codes:
            try:
                detail = self.mcp_auth_service.check_mcp_permission_detail(user_id, code)
                mcp_details[code] = detail
                if not detail.get("has_permission"):
                    all_authorized = False
            except Exception:
                logger.exception("[SkillAuth][bot] check failed for server_code=%s", code)
                mcp_details[code] = {
                    "has_permission": False,
                    "access_level": "",
                    "tool_permissions": {},
                    "error": f"查询失败: {code}",
                }
                all_authorized = False

        return {
            "bot_id": bot_id,
            "authorized": all_authorized,
            "mcp_details": mcp_details,
        }

    def apply_bot_permission(
        self,
        user_id: str,
        bot_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """为用户批量申请 Bot 所有 MCP 依赖的权限。

        每个 MCP server 只申请一次。

        Args:
            user_id: 用户工号
            bot_id: Bot 标识 (对应 ac_skill_set.bolt_id)
            reason: 申请原因

        Returns:
            {
                "bot_id": str,
                "all_authorized": bool,
                "apply_results": {
                    "<server_code>": {
                        "success": bool,
                        "process_url": str | None,
                        "error": str | None,
                        "already_authorized": bool,
                    }
                }
            }
        """

        _skill_sets, server_codes = self._collect_bot_mcp_codes(bot_id)

        if not server_codes:
            return {
                "bot_id": bot_id,
                "all_authorized": True,
                "apply_results": {},
            }

        auth_svc = self.mcp_auth_service
        apply_results: dict[str, Any] = {}
        all_ok = True

        for code in server_codes:
            try:
                mcp_data = self.mcp_center.get_mcp_detail(code)
                if not mcp_data:
                    apply_results[code] = {
                        "success": False,
                        "process_url": None,
                        "error": f"MCP server not found: {code}",
                        "already_authorized": False,
                    }
                    all_ok = False
                    continue

                access_level = mcp_data.get("accessLevel", "")
                is_public = access_level == "PUBLIC"
                tools = mcp_data.get("tools", [])
                tool_names = [t["name"] for t in tools if isinstance(t, dict) and t.get("name")]

                result = auth_svc.apply_permission(
                    staff_no=user_id,
                    service_code=code,
                    tool_list=tool_names,
                    is_public=is_public,
                    reason=reason,
                )

                already_authorized = (
                    result.get("success") and result.get("process_url") is None
                )
                apply_results[code] = {
                    "success": result.get("success", False),
                    "process_url": result.get("process_url"),
                    "error": result.get("error"),
                    "already_authorized": already_authorized,
                }
                if not result.get("success"):
                    all_ok = False

            except Exception:
                logger.exception("[SkillAuth][bot] apply failed for server_code=%s", code)
                apply_results[code] = {
                    "success": False,
                    "process_url": None,
                    "error": f"申请失败: {code}",
                    "already_authorized": False,
                }
                all_ok = False

        return {
            "bot_id": bot_id,
            "all_authorized": all_ok,
            "apply_results": apply_results,
        }

    # ----------------------------------------------------------
    # SkillSet 级别
    # ----------------------------------------------------------

    def _collect_skill_set_mcp_codes(
        self, skill_set_id: str
    ) -> tuple[list[dict], list[str], dict[str, list[str]]]:
        """获取 skill_set 下所有 skill 并汇总去重的 MCP server_code。

        Returns:
            (skills, all_server_codes, skill_to_codes)
            - skills: skill_set 内的 skill 列表
            - all_server_codes: 去重后的全局 server_code 列表
            - skill_to_codes: { skill_id: [server_code, ...] }

        Raises:
            ValueError: skill_set 不存在或无法获取
        """
        skills = self.skill_set_repo.get_skills_in_set(skill_set_id)
        if skills is None:
            raise ValueError(f"SkillSet not found: {skill_set_id}")

        seen = set()
        all_codes: list[str] = []
        skill_to_codes: dict[str, list[str]] = {}

        for sk in skills:
            codes = self._extract_server_codes(sk)
            skill_to_codes[str(sk.get("id", ""))] = codes
            for c in codes:
                if c not in seen:
                    seen.add(c)
                    all_codes.append(c)

        return skills, all_codes, skill_to_codes

    def check_skill_set_permission(
        self, user_id: str, skill_set_id: str
    ) -> dict[str, Any]:
        """检查用户对 SkillSet 内所有 Skill 的 MCP 依赖权限。

        每个 MCP server 只查询一次，结果按 skill 维度聚合。

        Returns:
            {
                "skill_set_id": str,
                "authorized": bool,
                "skills": {
                    "<skill_id>": {
                        "skill_name": str,
                        "authorized": bool,
                        "mcp_details": { "<server_code>": { ... } }
                    }
                },
                "mcp_summary": {
                    "<server_code>": { "has_permission": bool, ... }
                }
            }
        """
        skills, all_codes, skill_to_codes = self._collect_skill_set_mcp_codes(skill_set_id)

        # 1. 批量查询所有唯一 MCP server（每个只查一次）
        mcp_cache: dict[str, dict[str, Any]] = {}
        for code in all_codes:
            try:
                mcp_cache[code] = self.mcp_auth_service.check_mcp_permission_detail(user_id, code)
            except Exception:
                logger.exception("[SkillAuth][set] check failed for server_code=%s", code)
                mcp_cache[code] = {
                    "has_permission": False,
                    "access_level": "",
                    "tool_permissions": {},
                    "error": f"查询失败: {code}",
                }

        # 2. 按 skill 聚合
        all_authorized = True
        skills_result: dict[str, Any] = {}

        for sk in skills:
            sid = str(sk.get("id", ""))
            codes = skill_to_codes.get(sid, [])
            sk_authorized = True
            sk_details: dict[str, Any] = {}

            for c in codes:
                detail = mcp_cache.get(c, {})
                sk_details[c] = detail
                if not detail.get("has_permission"):
                    sk_authorized = False

            if not sk_authorized:
                all_authorized = False

            skills_result[sid] = {
                "skill_name": sk.get("name", ""),
                "authorized": sk_authorized,
                "mcp_details": sk_details,
            }

        return {
            "skill_set_id": skill_set_id,
            "authorized": all_authorized,
            "skills": skills_result,
            "mcp_summary": mcp_cache,
        }

    def apply_skill_set_permission(
        self,
        user_id: str,
        skill_set_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """为用户批量申请 SkillSet 内所有 MCP 依赖的权限。

        每个 MCP server 只申请一次，结果按 skill 维度聚合。

        Returns:
            {
                "skill_set_id": str,
                "all_authorized": bool,
                "skills": {
                    "<skill_id>": {
                        "skill_name": str,
                        "all_authorized": bool,
                        "mcp_codes": [str, ...]
                    }
                },
                "apply_results": {
                    "<server_code>": {
                        "success": bool,
                        "process_url": str | None,
                        "error": str | None,
                        "already_authorized": bool,
                    }
                }
            }
        """

        skills, all_codes, skill_to_codes = self._collect_skill_set_mcp_codes(skill_set_id)

        if not all_codes:
            skills_result = {
                str(sk.get("id", "")): {
                    "skill_name": sk.get("name", ""),
                    "all_authorized": True,
                    "mcp_codes": [],
                }
                for sk in skills
            }
            return {
                "skill_set_id": skill_set_id,
                "all_authorized": True,
                "skills": skills_result,
                "apply_results": {},
            }

        auth_svc = self.mcp_auth_service
        apply_results: dict[str, Any] = {}
        all_ok = True

        for code in all_codes:
            try:
                mcp_data = self.mcp_center.get_mcp_detail(code)
                if not mcp_data:
                    apply_results[code] = {
                        "success": False,
                        "process_url": None,
                        "error": f"MCP server not found: {code}",
                        "already_authorized": False,
                    }
                    all_ok = False
                    continue

                access_level = mcp_data.get("accessLevel", "")
                is_public = access_level == "PUBLIC"
                tools = mcp_data.get("tools", [])
                tool_names = [t["name"] for t in tools if isinstance(t, dict) and t.get("name")]

                result = auth_svc.apply_permission(
                    staff_no=user_id,
                    service_code=code,
                    tool_list=tool_names,
                    is_public=is_public,
                    reason=reason,
                )

                already_authorized = (
                    result.get("success") and result.get("process_url") is None
                )
                apply_results[code] = {
                    "success": result.get("success", False),
                    "process_url": result.get("process_url"),
                    "error": result.get("error"),
                    "already_authorized": already_authorized,
                }
                if not result.get("success"):
                    all_ok = False

            except Exception:
                logger.exception("[SkillAuth][set] apply failed for server_code=%s", code)
                apply_results[code] = {
                    "success": False,
                    "process_url": None,
                    "error": f"申请失败: {code}",
                    "already_authorized": False,
                }
                all_ok = False

        # 按 skill 聚合结果
        skills_result: dict[str, Any] = {}

        for sk in skills:
            sid = str(sk.get("id", ""))
            codes = skill_to_codes.get(sid, [])
            sk_ok = all(
                apply_results.get(c, {}).get("success", False) for c in codes
            ) if codes else True

            skills_result[sid] = {
                "skill_name": sk.get("name", ""),
                "all_authorized": sk_ok,
                "mcp_codes": codes,
            }

        return {
            "skill_set_id": skill_set_id,
            "all_authorized": all_ok,
            "skills": skills_result,
            "apply_results": apply_results,
        }
