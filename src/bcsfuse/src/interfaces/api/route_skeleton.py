"""
Route skeleton helper for HTTP 501 Not Implemented responses.

This module provides a helper function for route skeleton routes that are
declared for OpenAPI parity but not yet implemented with business logic.
"""

from fastapi import HTTPException, status


def raise_not_implemented(feature: str, phase: str, detail: str | None = None) -> None:
    """
    Raise HTTP 501 Not Implemented for route skeleton.

    Args:
        feature: The feature/route name (e.g., "recommend", "fusion", "verify.batch")
        phase: Implementation phase (e.g., "R1", "R2", "R3")
        detail: Optional additional detail message

    Raises:
        HTTPException: Always raises 501 with structured error response

    Example:
        @app.post("/api/v1/recommend")
        async def recommend_bots(request: BotRecommendationRequest):
            raise_not_implemented("recommend", "R1")
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "NOT_IMPLEMENTED",
            "feature": feature,
            "phase": phase,
            "message": detail or f"{feature} is declared for route parity but not implemented yet.",
        },
    )