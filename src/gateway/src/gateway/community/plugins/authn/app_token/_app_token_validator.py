"""StubAppTokenValidator — open-source single-box stub for the app-token validator.

Accepts ``stub-app-token`` and returns a fixed in-memory record; any other
token yields ``None``. NOT real validation — production uses the sofa (baas)
flavor.
"""

from __future__ import annotations

from gateway.community.spi.authn import AppTokenRecord, AppTokenValidator

_STUB_TOKEN = "stub-app-token"


class StubAppTokenValidator(AppTokenValidator):
    """Single-box stub: one hard-coded valid app token."""

    async def verify(self, app_token: str) -> AppTokenRecord | None:
        if app_token != _STUB_TOKEN:
            return None
        return AppTokenRecord(
            app_id="stub-app",
            app_name="Stub App",
            owners="stub-org",
            app_type="stub",
            tenant="stub_tenant",
        )
