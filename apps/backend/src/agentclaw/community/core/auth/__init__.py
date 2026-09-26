"""Auth layer — public user identity models.

FastAPI dependencies (``get_current_user``, ``require_operator``,
``_build_auth_context``) live under ``api/auth/dependencies.py`` per
Rule 7 (Core Independence).
"""
from agentclaw.community.core.auth.models import AuthenticatedIdentity

__all__ = ["AuthenticatedIdentity"]
