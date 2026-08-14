"""``google_token`` strategy — verify a presented Google access token via userinfo."""

from ._strategy import (
    GOOGLE_USERINFO_URL,
    USERINFO_TIMEOUT_SECONDS,
    GoogleUserStrategy,
)

__all__ = [
    "GOOGLE_USERINFO_URL",
    "GoogleUserStrategy",
    "USERINFO_TIMEOUT_SECONDS",
]
