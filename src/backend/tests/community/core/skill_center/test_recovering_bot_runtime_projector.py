"""Common projection completion must wake durable Desktop Skill recovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionIssue,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.services.recovering_bot_runtime_projector import (
    RecoveringBotRuntimeProjector,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["project", "apply_plan"])
async def test_unresolved_skill_projection_ensures_common_recovery(method) -> None:
    delegate = MagicMock()
    setattr(
        delegate,
        method,
        AsyncMock(
            return_value=RuntimeProjectionResult.pending(
                code="CENTER_CONTENT_DOWNLOAD_PENDING",
                reason="download is active",
            )
        ),
    )
    recovery = MagicMock()
    projector = RecoveringBotRuntimeProjector(
        delegate=delegate,
        recovery=recovery,
    )
    kwargs = {
        "scope": ProjectionScope(skills=True),
        "bot_id": "desktop-1",
        "owner_id": "owner-1",
    }
    if method == "apply_plan":
        kwargs = {
            "scope": ProjectionScope(skills=True),
            "plan": MagicMock(bot_id="desktop-1", owner_id="owner-1"),
        }

    result = await getattr(projector, method)(**kwargs)

    assert result.status.value == "PENDING"
    recovery.ensure.assert_called_once_with(
        owner_id="owner-1", bot_id="desktop-1"
    )


@pytest.mark.asyncio
async def test_converged_or_mcp_only_projection_does_not_ensure_recovery() -> None:
    delegate = MagicMock()
    delegate.project = AsyncMock(
        side_effect=[
            RuntimeProjectionResult.converged(),
            RuntimeProjectionResult.pending(code="MCP_PENDING", reason="pending"),
        ]
    )
    recovery = MagicMock()
    projector = RecoveringBotRuntimeProjector(
        delegate=delegate,
        recovery=recovery,
    )

    await projector.project(
        bot_id="desktop-1",
        owner_id="owner-1",
        scope=ProjectionScope(skills=True),
    )
    await projector.project(
        bot_id="desktop-1",
        owner_id="owner-1",
        scope=ProjectionScope(mcp=True),
    )

    recovery.ensure.assert_not_called()


@pytest.mark.asyncio
async def test_mixed_projection_with_only_mcp_pending_does_not_ensure_skill_recovery():
    delegate = MagicMock()
    delegate.project = AsyncMock(
        return_value=RuntimeProjectionResult(
            status=RuntimeProjectionStatus.PENDING,
            components={
                "skills": RuntimeProjectionStatus.CONVERGED,
                "mcp": RuntimeProjectionStatus.PENDING,
            },
            issues=(
                RuntimeProjectionIssue(
                    resource_type="MCP",
                    code="MCP_RUNTIME_UNAVAILABLE",
                    reason="MCP delivery failed",
                    status=RuntimeProjectionStatus.PENDING,
                    retryable=True,
                ),
            ),
        )
    )
    recovery = MagicMock()
    projector = RecoveringBotRuntimeProjector(
        delegate=delegate,
        recovery=recovery,
    )

    await projector.project(
        bot_id="desktop-1",
        owner_id="owner-1",
        scope=ProjectionScope(skills=True, mcp=True),
    )

    recovery.ensure.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["project", "apply_plan"])
async def test_skill_projection_exception_ensures_then_preserves_error(method) -> None:
    delegate = MagicMock()
    setattr(delegate, method, AsyncMock(side_effect=RuntimeError("runtime failed")))
    recovery = MagicMock()
    projector = RecoveringBotRuntimeProjector(
        delegate=delegate,
        recovery=recovery,
    )
    kwargs = {
        "scope": ProjectionScope(skills=True),
        "bot_id": "desktop-1",
        "owner_id": "owner-1",
    }
    if method == "apply_plan":
        kwargs = {
            "scope": ProjectionScope(skills=True),
            "plan": MagicMock(bot_id="desktop-1", owner_id="owner-1"),
        }

    with pytest.raises(RuntimeError, match="runtime failed"):
        await getattr(projector, method)(**kwargs)

    recovery.ensure.assert_called_once_with(
        owner_id="owner-1", bot_id="desktop-1"
    )


@pytest.mark.asyncio
async def test_recovery_enqueue_failure_is_logged_and_returned_distinctly(
    caplog,
) -> None:
    delegate = MagicMock()
    delegate.project = AsyncMock(
        return_value=RuntimeProjectionResult.pending(
            code="CENTER_CONTENT_DOWNLOAD_PENDING",
            reason="download is active",
        )
    )
    recovery = MagicMock()
    recovery.ensure.side_effect = RuntimeError("task database unavailable")
    projector = RecoveringBotRuntimeProjector(
        delegate=delegate,
        recovery=recovery,
    )

    result = await projector.project(
        bot_id="desktop-1",
        owner_id="owner-1",
        scope=ProjectionScope(skills=True),
    )

    assert {issue.code for issue in result.issues} == {
        "CENTER_CONTENT_DOWNLOAD_PENDING",
        "DESKTOP_SKILL_RECOVERY_ENQUEUE_FAILED",
    }
    assert "task database unavailable" in caplog.text
