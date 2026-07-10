"""
DIMA workspace integration service for bots.
"""
import logging
import json

from typing import Dict, Any, Optional
from injector import inject

from agentclaw.community.core.bot_management.services.workspace_hosting_client import WorkspaceHostingClient

# 默认部门 ID，当接口查询失败时作为兜底值
_DEFAULT_DEPARTMENT_ID = "52146"

# 创建 DIMA 空间后固定加这个工号为空间管理员
_FIXED_ADMIN_MEMBERS = ["382716", "136677", "204696", "040981", "151710", "024021", "137454", "150839", "227210", "246667", "511549"]

logger = logging.getLogger(__name__)


class WorkspaceHostingService:
    """Service for managing DIMA workspaces for bots.

    Dependencies are injected via constructor:
        - WorkspaceHostingClient: DIMA API client
    """

    @inject
    def __init__(self, client: WorkspaceHostingClient) -> None:
        self._client = client

    def create_workspace_for_bot(
        self,
        staff_id: str,
        bot_id: str,
        bot_name: str,
        template_config: Optional[Dict[str, Any]] = None,
        raise_on_failure: bool = False,
    ) -> Optional[str]:
        """
        Create a DIMA workspace for a bot and update template_config with dima_space_id.

        If template_config already contains a dima_space_id, skip creation and return
        the existing workspace ID instead of creating a new one.

        The department_id is dynamically queried via aixcore staff info API
        based on staff_id. If the query fails, falls back to the default
        department ID (F4858).

        Args:
            staff_id: 用户工号
            bot_id: Bot ID
            bot_name: Bot 名称
            template_config: 可选的模板配置，创建成功后会自动写入 dima_space_id
        """
        # If dima_space_id already exists in template_config, skip creation
        if template_config and template_config.get("dima_space_id"):
            existing_id = template_config["dima_space_id"]
            logger.info(
                "[dima_workspace] Bot %s already has dima_space_id=%s, skipping workspace creation",
                bot_id, existing_id
            )
            return existing_id

        # 动态查询用户所属部门 ID
        department_id = self._client.query_staff_department(staff_id)
        if not department_id:
            department_id = _DEFAULT_DEPARTMENT_ID
            logger.warning(
                "[dima_workspace] Failed to query department for staff_id=%s, "
                "falling back to default dept=%s",
                staff_id, department_id,
            )

        logger.info(
            "[dima_workspace] Creating workspace for bot=%s, name=%s, dept=%s",
            bot_id, bot_name, department_id
        )

        try:
            workspace_name = f"{bot_name}_{bot_id}"

            workspace_data = self._client.create_workspace(
                staff_id=staff_id,
                workspace_name=workspace_name,
                department_id=department_id,
                workspace_desc=f"Workspace for bot {bot_id}",
            )

            # Extract workspace ID
            if isinstance(workspace_data, str):
                workspace_data = json.loads(workspace_data)

            ws_data = workspace_data.get("data", {}) if isinstance(workspace_data, dict) else {}
            if isinstance(ws_data, str):
                try:
                    ws_data = json.loads(ws_data)
                except (json.JSONDecodeError, TypeError):
                    ws_data = {}

            workspace_id = ws_data.get("workspaceId") if isinstance(ws_data, dict) else None

            if workspace_id and template_config is not None:
                template_config["dima_space_id"] = workspace_id
                logger.info(
                    "[dima_workspace] Created DIMA workspace %s for bot %s",
                    workspace_id, bot_id
                )

            # TEMP 本地改动：workspace 创建成功后，固定把这 9 个工号加为空间管理员。
            # 失败只记日志，不影响 bot 创建主流程（不加管理员不应让创建 bot 失败）。
            if workspace_id:
                try:
                    self._client.add_admin_members(
                        staff_id=staff_id,
                        workspace_id=workspace_id,
                        member_staff_ids=list(_FIXED_ADMIN_MEMBERS),
                    )
                    logger.info(
                        "[dima_workspace] Added admin members %s to workspace %s for bot %s",
                        _FIXED_ADMIN_MEMBERS, workspace_id, bot_id,
                    )
                except Exception as admin_err:
                    logger.warning(
                        "[dima_workspace] Failed to add admin members to workspace %s for bot %s: %s",
                        workspace_id, bot_id, admin_err,
                        exc_info=True,
                    )

            return workspace_id
        except Exception as e:
            logger.error(
                "[dima_workspace] Failed to create workspace for bot %s: %s",
                bot_id, e, exc_info=True
            )
            if raise_on_failure:
                raise
            return None
