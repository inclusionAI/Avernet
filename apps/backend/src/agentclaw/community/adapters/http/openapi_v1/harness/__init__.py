from __future__ import annotations

from fastapi import APIRouter

from .router import router as harness_router


def build_harness_router() -> APIRouter:
    return harness_router


__all__ = ["build_harness_router", "harness_router"]
