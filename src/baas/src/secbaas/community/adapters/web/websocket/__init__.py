"""WebSocket handlers for local management and other real-time communication."""

from .local_management_ws import router as local_management_router

__all__ = ["local_management_router"]
