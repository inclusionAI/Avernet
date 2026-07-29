"""BareAppTokenValidator — open-source single-box stub for the app-token validator.

Accepts ``bare-app-token`` and returns a fixed in-memory record; any other
token yields ``None``. NOT real validation — production uses the sofa (baas)
flavor.
"""

from __future__ import annotations

from gateway.community.spi.authn import AppTokenRecord, AppTokenValidator

_BARE_TOKEN = "bare-app-token"


class BareAppTokenValidator(AppTokenValidator):
    """Single-box stub: one hard-coded valid app token."""

    async def verify(self, app_token: str) -> AppTokenRecord | None:
        if app_token != _BARE_TOKEN:
            return None
        return AppTokenRecord(
            app_id="bare-app",
            app_name="Bare App",
            owners="bare-org",
            app_type="bare",
            tenant="tenant-bare",
        )
