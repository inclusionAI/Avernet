"""Collaborator authorization for Bot MCP operations."""

from agentclaw.community.core.bot_collaborator.models import PermissionLevel

from .models_authorization import EDIT_LOCK, Authorization, Check

BOT_MCP_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("GET", "/openapi/v1/bots/{bot_id}/mcps"): Check(PermissionLevel.MEMBER),
    ("GET", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/config"): Check(
        PermissionLevel.MEMBER
    ),
    ("PUT", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/config"): Check(
        PermissionLevel.ADMIN, EDIT_LOCK, explicit=True
    ),
    ("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate"): Check(
        PermissionLevel.MEMBER, explicit=True
    ),
    ("POST", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate"): Check(
        PermissionLevel.MEMBER, explicit=True
    ),
    ("PATCH", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type"): Check(
        PermissionLevel.OWNER, EDIT_LOCK
    ),
}
