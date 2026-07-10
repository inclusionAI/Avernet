from __future__ import annotations

from tests.community.acceptance.devices import test_device_query_lifecycle


def test_wait_device_active_retries_transient_http_error(monkeypatch):
    class Response:
        def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = text

        def json(self) -> dict:
            return self._payload

    class Client:
        def __init__(self) -> None:
            self.responses = [
                Response(503, {}, "temporarily unavailable"),
                Response(200, {"success": True, "data": {"status": "ACTIVE"}}),
            ]

        def get(self, _path: str) -> Response:
            return self.responses.pop(0)

    monkeypatch.setattr(test_device_query_lifecycle.time, "sleep", lambda _seconds: None)

    assert test_device_query_lifecycle.wait_device_active(
        Client(), 7, timeout_sec=1
    ) == {"status": "ACTIVE"}
