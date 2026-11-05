"""The agentswarm chat transport seam.

``base`` states the contract; each sibling module implements it over one
channel. Import from here rather than from an implementation module, so callers
never bind themselves to a particular channel.
"""

from .base import (
    ChatError,
    ChatRateLimitedError,
    ChatRefusedError,
    ChatTimeoutError,
    ChatTransport,
    ChatUnavailableError,
    SessionMode,
)

__all__ = [
    "BaasGatewayChatTransport",
    "ChatError",
    "ChatRateLimitedError",
    "ChatRefusedError",
    "ChatTimeoutError",
    "ChatTransport",
    "ChatUnavailableError",
    "SessionMode",
]


def __getattr__(name: str):
    """Lazily resolve the BaaS transport.

    Importing it eagerly would pull ``httpx`` (and the gateway principal signer)
    into every process that merely touches the chat package — including the
    engine-channel path, which needs neither.
    """
    if name == "BaasGatewayChatTransport":
        from .baas_gateway import BaasGatewayChatTransport

        globals()[name] = BaasGatewayChatTransport
        return BaasGatewayChatTransport
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")