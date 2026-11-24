"""Reuse Bot read/operations policy; no promotion/apply authority is granted.

System rules require explicit collaborator membership for mutating operations.
Space-synthesized chat/read membership is not permission to edit or run tasks.
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
    ("GET", "/strategies"),
    ("GET", "/candidates/{candidate_id}"),
    ("GET", "/review-queue"),
    ("POST", "/candidates/{candidate_id}:approve"),
    ("POST", "/candidates/{candidate_id}:reject"),
)
AUTHORIZATION = {
    (method, BASE + suffix): Check(PermissionLevel.MEMBER, explicit=method != "GET")
    for method, suffix in OPERATIONS
}
ADMISSION = {
    (method, BASE + suffix): AdmissionMode.GRANT_CHECKED_ADDRESSED_BOT
    for method, suffix in OPERATIONS
}
