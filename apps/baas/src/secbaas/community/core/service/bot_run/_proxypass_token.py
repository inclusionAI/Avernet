"""Target-bound proxypass token minting results for bot runs.

The public service layer mints a proxy token for a concrete WebSocket target.
Returning a bare token would allow callers to attach a token to a stale URI after
a device/routing change. These small internal result types make the target an
explicit part of the result without adding a public API.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProxypassTargetMismatchError(Exception):
    """Raised when a freshly minted token belongs to a different target URI."""


@dataclass(frozen=True, slots=True)
class ProxypassTokenMint:
    """A successful mint whose token is bound to ``connection_uri``."""

    token: str
    connection_uri: str

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not self.token:
            raise ValueError("ProxypassTokenMint.token must be a non-empty string")
        if not isinstance(self.connection_uri, str) or not self.connection_uri:
            raise ValueError(
                "ProxypassTokenMint.connection_uri must be a non-empty string"
            )


@dataclass(frozen=True, slots=True)
class NoProxypassToken:
    """The target intentionally operates without a proxypass token."""

    connection_uri: str

    def __post_init__(self) -> None:
        if not isinstance(self.connection_uri, str) or not self.connection_uri:
            raise ValueError(
                "NoProxypassToken.connection_uri must be a non-empty string"
            )
