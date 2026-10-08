"""``dev_header`` strategy — LOCAL-ONLY user identity from the x-dev-user header."""

from ._strategy import (
    AUTH_MOCK_ENV,
    DEV_USER_HEADER,
    DevHeaderUserStrategy,
    auth_mock_enabled,
)

__all__ = [
    "AUTH_MOCK_ENV",
    "DEV_USER_HEADER",
    "DevHeaderUserStrategy",
    "auth_mock_enabled",
]
