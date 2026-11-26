"""Token usage HTTP router — operator-only Bot token statistics.

Endpoints:
- ``GET /api/v1/bots/token-usage/summary`` — batch summary
- ``GET /api/v1/bots/{bot_id}/token-usage/daily`` — per-Bot daily trend

All endpoints require operator (admin) authentication.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.bot_chat.schemas import ApiResponse
from agentclaw.community.core.bot_chat.token_usage_service import TokenUsageService
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/v1/bots", tags=["bot-token-usage"])

_MS_PER_DAY = 86_400_000


def _resolve_time_range(
    period: str,
    from_ms: int | None,
    to_ms: int | None,
) -> tuple[int, int]:
    """Resolve the query window from a preset period or an explicit range."""
    if period == "custom":
        if from_ms is None or to_ms is None or from_ms >= to_ms:
            raise ValueError("自定义时间段需要提供有效的 from_ms 与 to_ms")
        return from_ms, to_ms

    now_ms = int(time.time() * 1000)
    days = 30 if period == "30d" else 7
    return now_ms - days * _MS_PER_DAY, now_ms


@router.get("/token-usage/summary", response_model=ApiResponse[list[dict]])
async def get_bots_token_usage_summary(
    bot_ids: Annotated[
        str | None,
        Query(description="Comma-separated bot IDs; omit to return all bots with data"),
    ] = None,
    from_ms: Annotated[
        int | None,
        Query(description="Start time epoch ms (required for period=custom)"),
    ] = None,
    to_ms: Annotated[
        int | None,
        Query(description="End time epoch ms (required for period=custom)"),
    ] = None,
    period: Annotated[
        str,
        Query(pattern="^(7d|30d|custom)$", description="Preset period: 7d / 30d / custom"),
    ] = "7d",
    _user: AuthenticatedUser = Depends(require_operator),
    service: TokenUsageService = Injected(TokenUsageService),
) -> ApiResponse[list[dict]]:
    """Aggregated token usage for all (or selected) bots."""
    try:
        start_ms, end_ms = _resolve_time_range(period, from_ms, to_ms)
        parsed_bot_ids: list[str] | None = None
        if bot_ids:
            parsed_bot_ids = [bid.strip() for bid in bot_ids.split(",") if bid.strip()]

        rows = service.bots_token_summary(
            bot_ids=parsed_bot_ids,
            from_ms=start_ms,
            to_ms=end_ms,
        )
        return ApiResponse(success=True, message="ok", data=rows)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=400, data=[])
    except Exception:
        logger.exception("[token_usage.summary] query failed")
        return ApiResponse(
            success=False,
            message="查询 Token 用量失败",
            error_code=500,
            data=[],
        )


@router.get("/{bot_id}/token-usage/daily", response_model=ApiResponse[list[dict]])
async def get_bot_token_usage_daily(
    bot_id: str,
    from_ms: Annotated[
        int | None,
        Query(description="Start time epoch ms (required for period=custom)"),
    ] = None,
    to_ms: Annotated[
        int | None,
        Query(description="End time epoch ms (required for period=custom)"),
    ] = None,
    period: Annotated[
        str,
        Query(pattern="^(7d|30d|custom)$", description="Preset period: 7d / 30d / custom"),
    ] = "7d",
    _user: AuthenticatedUser = Depends(require_operator),
    service: TokenUsageService = Injected(TokenUsageService),
) -> ApiResponse[list[dict]]:
    """Daily token usage trend for a single bot."""
    try:
        start_ms, end_ms = _resolve_time_range(period, from_ms, to_ms)
        rows = service.bot_token_daily(
            bot_id=bot_id,
            from_ms=start_ms,
            to_ms=end_ms,
        )
        return ApiResponse(success=True, message="ok", data=rows)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=400, data=[])
    except Exception:
        logger.exception("[token_usage.daily] query failed bot_id=%s", bot_id)
        return ApiResponse(
            success=False,
            message="查询 Token 趋势失败",
            error_code=500,
            data=[],
        )
