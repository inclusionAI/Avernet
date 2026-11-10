"""Authorization rules for the public service-Bot evaluation API."""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.models_authorization import Check
from agentclaw.community.core.bot_collaborator.models import PermissionLevel

SERVICE_EVAL_AUTHORIZATION = {
    ("GET", "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}"): Check(
        PermissionLevel.MEMBER
    ),
    ("POST", "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/simple-cert/precheck"): Check(
        PermissionLevel.MEMBER, explicit=True
    ),
    ("POST", "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/simple-cert/submissions"): Check(
        PermissionLevel.OWNER
    ),
    ("GET", "/openapi/v1/bots/{bot_id}/eval/submissions/{submission_id}"): Check(
        PermissionLevel.MEMBER
    ),
    ("GET", "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/history"): Check(
        PermissionLevel.MEMBER
    ),
    ("POST", "/openapi/v1/bots/{bot_id}/eval/versions/{bot_version}/simple-cert/sync"): Check(
        PermissionLevel.MEMBER, explicit=True
    ),
}
