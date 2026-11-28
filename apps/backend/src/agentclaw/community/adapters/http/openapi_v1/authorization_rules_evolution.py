"""Reuse Bot policy; explicit human authorization and Apply require ADMIN.

System rules require explicit collaborator membership for mutating operations.
Space-synthesized chat/read membership is not permission to edit or run tasks.
Risk-review approval does not grant delivery; manual authorization and Apply are separate.
"""

from agentclaw.community.core.bot_collaborator.models import PermissionLevel

from .admission_modes import AdmissionMode
from .models_authorization import Check

BASE = "/openapi/v1/bots/{bot_id}/evolution"
OPERATIONS = (
    ("GET", "/bindings"),
    ("PUT", "/bindings"),
    ("POST", "/runs"),
    ("GET", "/runs"),
    ("GET", "/runs/{run_id}"),
    ("POST", "/runs/{run_id}:cancel"),
    ("GET", "/runs/{run_id}/candidates"),
    ("GET", "/runs/{run_id}/evidence"),
    ("GET", "/runs/{run_id}/strategy-output"),
    ("GET", "/strategies"),
    ("GET", "/candidates/{candidate_id}"),
    ("GET", "/review-queue"),
    ("POST", "/candidates/{candidate_id}:approve"),
    ("POST", "/candidates/{candidate_id}:reject"),
    ("POST", "/candidates/{candidate_id}:authorize-promotion"),
    ("POST", "/candidates/{candidate_id}:apply"),
)
AUTHORIZATION = {
    (method, BASE + suffix): Check(
        PermissionLevel.ADMIN if suffix.endswith((":authorize-promotion", ":apply")) else PermissionLevel.MEMBER,
        explicit=method != "GET",
    )
    for method, suffix in OPERATIONS
}
ADMISSION = {
    (method, BASE + suffix): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    for method, suffix in OPERATIONS
}
