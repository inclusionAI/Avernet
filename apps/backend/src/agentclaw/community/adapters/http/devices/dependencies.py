"""Device dependencies — FastAPI ``Depends`` chain for OperatorContext.

``get_operator_context`` is a FastAPI ``Depends`` chain rather than an
injector binding — mixing fastapi-injector's request scope with
FastAPI's auth dependency chain is awkward, and a plain Depends does
the job. This file lives under ``api/`` (Rule 7 — Core Independence):
``core/`` must not import from ``fastapi``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends

from agentclaw.community.adapters.http.auth.dependencies import get_current_user as _get_current_user
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.adapters.http.auth.models import AuthenticatedUser

logger = get_logger()


def _compose_operator_context(user: "AuthenticatedUser") -> OperatorContext:
    return OperatorContext(
        staff_id=user.staffId,
        staff=user.operatorName,
        nick_name=user.nickName,
        operator_name=user.operatorName,
        tenant_id=user.tenantId,
    )


def get_operator_context(
    user: "AuthenticatedUser" = Depends(_get_current_user),
) -> OperatorContext:
    """FastAPI dependency: convert authenticated user to OperatorContext."""
    return _compose_operator_context(user)
