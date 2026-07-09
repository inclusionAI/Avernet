"""Channel core module."""

__all__ = [
    "ChannelRecord",
    "ChannelRepository",
    "ChannelService",
]


def __getattr__(name: str):
    """Lazy import to avoid circular dependencies at module load time."""
    if name in ("ChannelRecord", "ChannelRepository"):
        from agentclaw.community.core.channel.services.repositories import (  # noqa: F401 lazy
            ChannelRecord,
            ChannelRepository,
        )
        return locals()[name]
    if name == "ChannelService":
        from agentclaw.community.core.channel.services.channel_service import ChannelService
        return ChannelService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
