"""Authorization decisions for the public Caller identity router.

Keep its context read, identity-mode writes and independent refresh together;
these rows are merged into the central fail-closed authorization inventory.
"""

from agentclaw.community.core.bot_collaborator.models import PermissionLevel

from .models_authorization import EDIT_LOCK, Authorization, Check

CALLER_IDENTITY_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("GET", "/openapi/v1/bots/{bot_id}/caller-context"): Check(PermissionLevel.MEMBER),
    ("PATCH", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type"): Check(
        PermissionLevel.OWNER, EDIT_LOCK
    ),
    ("PATCH", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type"): Check(
        PermissionLevel.OWNER, EDIT_LOCK
    ),
    ("POST", "/openapi/v1/bots/{bot_id}/caller-token/refresh"): Check(PermissionLevel.OWNER),
}
