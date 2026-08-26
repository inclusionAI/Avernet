"""Stub target resolver — fixed local destination for single-box development."""

from __future__ import annotations

STUB_DESTINATION = {
    "arca_host": "127.0.0.1:9999",
    "sandbox_id": "stub",
    "sandbox_port": "8080",
}


class StubTargetResolver:
    """No-op resolver: always returns a fixed local destination."""

    prefix = "stub"

    async def resolve(self, target_host: str) -> dict[str, str]:
        return dict(STUB_DESTINATION)
