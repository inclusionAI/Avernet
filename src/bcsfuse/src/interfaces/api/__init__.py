"""HTTP/REST API package with lazy compatibility exports.

Importing schema or route submodules must not construct the legacy application.
The historical package-level exports are loaded only when explicitly accessed.
"""

from typing import Any

__all__ = [
    "app",
    "worker_router",
]


def __getattr__(name: str) -> Any:
    if name == "app":
        from src.interfaces.api.app import app as application

        globals()[name] = application
        return application
    if name == "worker_router":
        from src.interfaces.api.worker_routes import router

        globals()[name] = router
        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
