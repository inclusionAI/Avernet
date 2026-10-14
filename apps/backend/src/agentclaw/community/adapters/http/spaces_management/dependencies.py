"""Shared super-admin admission for every Space maintenance endpoint."""

from fastapi import Depends

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.core.spaces.management_models import ManagementError


def require_space_super_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Authorize the authenticated actor, never a caller-supplied creator ID."""
    if not user.staffId or user.staffId == "anonymous":
        raise ManagementError("SPACE_AUTH_REQUIRED")
    if user.staffId not in super_admin():
        raise ManagementError("SPACE_ADMIN_REQUIRED")
    return user
