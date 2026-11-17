"""
Token usage API router.

Provides operator-only endpoints for Bot token consumption statistics:
- ``GET /api/v1/bots/token-usage/summary`` — batch summary
- ``GET /api/v1/bots/{bot_id}/token-usage/daily`` — per-Bot daily trend
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.bot_chat.token_usage_service import TokenUsageService
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/v1/bots", tags=["bot-token-usage"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Default time windows in milliseconds
_MS_PER_DAY = 86_400_000
_DEFAULT_PERIOD_MS = 7 * _MS_PER_DAY  # 7 days


def _resolve_time_range(
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
    period: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> tuple[int, int]:
    """Resolve from_ms / to_ms from query params, falling back to period.

    When *period* is ``7d`` or ``30d``, the range is computed relative to
    *now_ms* (or the current wall-clock time).  *from_ms* takes precedence
    when present.
    """
    import time

    if from_ms is not None and to_ms is not None:
        return from_ms, to_ms

    end = now_ms if now_ms is not None else int(time.time() * 1000)

    if period == "30d":
        start = end - 30 * _MS_PER_DAY
    else:
        # '7d' or absent — default to 7 days
        start = end - 7 * _MS_PER_DAY

    if from_ms is not None:
        start = from_ms
    if to_ms is not None:
        end = to_ms

    return start, end


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/token-usage/summary")
async def get_bots_token_usage_summary(
    from_ms: Optional[int] = Query(default=None, description="Start time epoch ms"),
    to_ms: Optional[int] = Query(default=None, description="End time epoch ms"),
    period: Optional[str] = Query(
        default="7d", pattern="^(7d|30d|custom)$",
        description="Time period: 7d / 30d / custom",
    ),
    bot_ids: Optional[str] = Query(
        default=None, description="Comma-separated bot IDs; omit for all",
    ),
    _user: AuthenticatedUser = Depends(require_operator),
    service: TokenUsageService = Injected(TokenUsageService),
) -> dict:
    """Aggregated token usage for one or more Bots."""
    start, end = _resolve_time_range(from_ms=from_ms, to_ms=to_ms, period=period)
    ids: Optional[list[str]] = None
    if bot_ids:
        ids = [b.strip() for b in bot_ids.split(",") if b.strip()]

    try:
        rows = service.batch_summary(from_ms=start, to_ms=end, bot_ids=ids)
    except Exception:
        logger.exception("[token_usage.summary] query failed")
        return {"success": False, "message": "查询 Token 用量失败", "error_code": 500, "data": None}

    return {
        "success": True,
        "message": "OK",
        "error_code": 200,
        "data": {  # pyright: ignore[reportUnknownArgumentType]
            "items": rows,
            "from_ms": start,
            "to_ms": end,
        },
    }


@router.get("/{bot_id}/token-usage/daily")
async def get_bot_token_usage_daily(
    bot_id: str,
    from_ms: Optional[int] = Query(default=None, description="Start time epoch ms"),
    to_ms: Optional[int] = Query(default=None, description="End time epoch ms"),
    period: Optional[str] = Query(
        default="7d", pattern="^(7d|30d|custom)$",
        description="Time period: 7d / 30d / custom",
    ),
    _user: AuthenticatedUser = Depends(require_operator),
    service: TokenUsageService = Injected(TokenUsageService),
) -> dict:
    """Daily token usage trend for a single Bot."""
    start, end = _resolve_time_range(from_ms=from_ms, to_ms=to_ms, period=period)

    try:
        rows = service.daily_trend(bot_id=bot_id, from_ms=start, to_ms=end)
    except Exception:
        logger.exception("[token_usage.daily] query failed bot_id=%s", bot_id)
        return {"success": False, "message": "查询 Token 趋势失败", "error_code": 500, "data": None}

    return {
        "success": True,
        "message": "OK",
        "error_code": 200,
        "data": {  # pyright: ignore[reportUnknownArgumentType]
            "bot_id": bot_id,
            "daily": rows,
            "from_ms": start,
            "to_ms": end,
        },
    }
