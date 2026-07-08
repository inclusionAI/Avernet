"""Local mock PassportPlugin."""
from __future__ import annotations

from typing import Any

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.passport import (
    CliItem,
    PassportPlugin,
    PassportResourceScope,
    SubResourceItem,
    extract_cli_items,
    unpack_resource_scope,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam


logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O",
)
class LocalPassportPlugin(MockSeam, PassportPlugin):
    """Local Mock 实现 —— 不代表 AgentPass 真集成。

    所有写操作仅打日志、apply_*/query_* 返回 mock token，**不**向 tcauthmng/AgentPass
    发任何请求。本地无法验证 AgentPass 端真实写入/生效；admin 同步的真实回执只能 prod 验证。
    """

    def update_passport(
        self,
        bot_id: str,
        user_id: str,
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        admins: list[str] | None = None,
        resource_scope: PassportResourceScope | None = None,
    ) -> None:
        mcp_codes, cli_items = unpack_resource_scope(resource_scope)
        logger.info(
            "[LocalPassportUpdate] update_passport: bot_id=%s, user_id=%s, "
            "mcp_codes=%s, cli_items=%s, engine_type=%s, access_mode=%s, "
            "admins=%s",
            bot_id,
            user_id,
            mcp_codes,
            cli_items,
            engine_type,
            access_mode,
            admins,
        )

    def apply_first_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
    ) -> dict[str, Any] | None:
        logger.info(
            "[LocalPassportUpdate] apply_first_agent_passport: bot_id=%s, "
            "owner_workno=%s, engine_type=%s, access_mode=%s, device_token=%s, "
            "workspace_path=%s, admins=%s, cli_items=%s",
            bot_id,
            owner_workno,
            engine_type,
            access_mode,
            device_token,
            workspace_path,
            admins,
            cli_items,
        )
        return {
            "token": f"mock_token_first_{bot_id}",
            "iframe_url": None,
            "redirect_url": None,
            "agent_code": f"local_{bot_id}",
        }

    def apply_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
    ) -> dict[str, Any] | None:
        logger.info(
            "[LocalPassportUpdate] apply_agent_passport: bot_id=%s, "
            "owner_workno=%s, engine_type=%s, access_mode=%s, device_token=%s, "
            "workspace_path=%s, admins=%s, cli_items=%s",
            bot_id,
            owner_workno,
            engine_type,
            access_mode,
            device_token,
            workspace_path,
            admins,
            cli_items,
        )
        return {
            "token": f"mock_token_nonfirst_{bot_id}",
            "iframe_url": None,
            "redirect_url": None,
            "agent_code": f"local_{bot_id}",
        }

    def destroy_passport(self, bot_id: str, owner_workno: str) -> None:
        logger.info(
            "[LocalPassportUpdate] destroy_passport: bot_id=%s, owner_workno=%s",
            bot_id,
            owner_workno,
        )

    def query_auth_status(self, bot_id: str, owner_workno: str) -> dict[str, Any] | None:
        logger.info(
            "[LocalPassportUpdate] query_auth_status: bot_id=%s, owner_workno=%s",
            bot_id,
            owner_workno,
        )
        return {
            "status": "ISSUED",
            "token": f"mock_token_status_{bot_id}",
        }

    def query_token(self, bot_id: str, owner_workno: str) -> str | None:
        logger.info(
            "[LocalPassportUpdate] query_token: bot_id=%s, owner_workno=%s",
            bot_id,
            owner_workno,
        )
        return f"mock_token_{bot_id}"

    def query_agent_passport(self, bot_id: str, owner_workno: str) -> dict[str, Any] | None:
        logger.info(
            "[LocalPassportUpdate] query_agent_passport: bot_id=%s, owner_workno=%s",
            bot_id,
            owner_workno,
        )
        return {
            "agent_id": bot_id,
            "agent_code": None,
            "credential_id": None,
            "expire_at": None,
            "engine_type": None,
            "access_mode": None,
            "mcps": [],
            "clis": [],
            "skills": [],
            "certificate_url": None,
            "admins": [],
        }

    def freeze_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> None:
        logger.info(
            "[LocalPassportUpdate] freeze_agent_passport: bot_id=%s, owner_workno=%s, reason=%s",
            bot_id,
            owner_workno,
            reason,
        )

    def unfreeze_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> None:
        logger.info(
            "[LocalPassportUpdate] unfreeze_agent_passport: bot_id=%s, owner_workno=%s, reason=%s",
            bot_id,
            owner_workno,
            reason,
        )

    def offline_agent_identity_credential(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> dict[str, Any] | None:
        logger.info(
            "[LocalPassportUpdate] offline_agent_identity_credential: "
            "bot_id=%s, owner_workno=%s, reason=%s",
            bot_id,
            owner_workno,
            reason,
        )
        return {
            "agent_code": f"local_{bot_id}",
            "principal_status": "OFFLINE",
        }

    def online_agent_identity_credential(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> dict[str, Any] | None:
        logger.info(
            "[LocalPassportUpdate] online_agent_identity_credential: "
            "bot_id=%s, owner_workno=%s, reason=%s",
            bot_id,
            owner_workno,
            reason,
        )
        return {
            "agent_code": f"local_{bot_id}",
            "principal_status": "ONLINE",
        }

    def query_passport_clis(
        self,
        bot_id: str,
        owner_workno: str,
    ) -> list[CliItem]:
        logger.info(
            "[LocalPassportUpdate] query_passport_clis: bot_id=%s, owner_workno=%s",
            bot_id,
            owner_workno,
        )
        return extract_cli_items(self.query_agent_passport(bot_id, owner_workno))

    def save_sub_resources(
        self,
        bot_id: str,
        owner_workno: str,
        sub_resources: list["SubResourceItem"],
    ) -> bool:
        # Local mock: no tcauthmng write, just log and succeed.
        logger.info(
            "[LocalPassportUpdate] save_sub_resources: bot_id=%s, count=%d",
            bot_id,
            len(sub_resources),
        )
        return True
