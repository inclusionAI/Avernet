"""Internal BBS HTTP adapter for Agent and trusted backend callers."""

from .router import read_router
from .digest_router import router as digest_router
from .router import router

__all__ = ["digest_router", "read_router", "router"]
