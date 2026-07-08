"""Channel API module."""

__all__ = ["router"]


def __getattr__(name: str):
    """Lazy import to avoid circular dependencies at module load time."""
    if name == "router":
        from agentclaw.community.adapters.http.channel.router import router
        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
