"""Unit tests for E2E hook polling compatibility behavior."""

from __future__ import annotations

from dataclasses import dataclass

from tests.e2e.hook_helpers import wait_for_publish_status


@dataclass
class _FakeResponse:
    status_code: int

    def json(self) -> dict[str, dict[str, str]]:
        return {"data": {"status": "FAILED"}}


class _FakeApiClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.calls = 0

    async def get(self, url: str, params: dict[str, str]) -> _FakeResponse:
        assert url == "/api/v1/publishes/123"
        response = self.responses[self.calls]
        self.calls += 1
        return response


class _FakeApi:
    def __init__(self, responses: list[_FakeResponse]):
        self.client = _FakeApiClient(responses)
        self._params = {"tenant_name": "team_claw"}

    def publish_url(self, publish_id: int) -> str:
        return f"/api/v1/publishes/{publish_id}"

    def params(self, **kwargs: str) -> dict[str, str]:
        return {**self._params, **kwargs}


async def test_not_found_keeps_legacy_success_compatibility() -> None:
    api = _FakeApi([_FakeResponse(404)])

    status = await wait_for_publish_status(
        api,
        123,
        {"SUCCESS", "FAILED"},
        timeout_seconds=0.01,
        poll_interval=0,
    )

    assert status == "SUCCESS"
    assert api.client.calls == 1


async def test_strict_status_polls_through_transient_not_found() -> None:
    api = _FakeApi([_FakeResponse(404), _FakeResponse(200)])

    status = await wait_for_publish_status(
        api,
        123,
        {"SUCCESS", "FAILED"},
        timeout_seconds=0.01,
        poll_interval=0,
        treat_not_found_as_success=False,
    )

    assert status == "FAILED"
    assert api.client.calls == 2
