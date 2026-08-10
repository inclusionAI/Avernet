"""Channel core module."""

__all__ = [
    "ChannelRecord",
    "ChannelRepository",
    "ChannelService",
]


def __getattr__(name: str):
    """Lazy import to avoid circular dependencies at module load time."""
    if name == "ChannelRecord":
        from agentclaw.community.core.channel.models import ChannelRecord

        return ChannelRecord
    if name == "ChannelRepository":
        from agentclaw.community.core.repository.protocols.chat import ChannelRepository

        return ChannelRepository
    if name == "ChannelService":
        from agentclaw.community.core.channel.services.channel_service import ChannelService
        return ChannelService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
