"""Unit tests for BaasService.list_bot_publishes (the adopt-by-query client)."""
import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)


class _Resp:
    def __init__(self, *, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = "err"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=httpx.Request("GET", "http://x"), response=self
            )

    def json(self):
        return self._json


class _Http:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def get(self, path, params=None, timeout=None):
        self.calls.append((path, params))
        return self._resp


def _svc(resp):
    s = object.__new__(BaasService)
    s._http = _Http(resp)
    s._tenant = "t"
    return s


def test_returns_publish_list():
    body = {"code": 0, "data": [
        {"id": 200, "bot_id": 1, "publish_type": "UPDATE", "status": "SUCCESS", "gmt_create": "x"},
        {"id": 100, "bot_id": 1, "publish_type": "CREATE", "status": "ACTIVE", "gmt_create": "y"},
    ]}
    svc = _svc(_Resp(json_body=body))
    out = svc.list_bot_publishes("BOT-1")
    assert [r["id"] for r in out] == [200, 100]
    assert svc._http.calls[0][0] == "/api/v1/bots/BOT-1/publishes"
    assert svc._http.calls[0][1]["tenant"] == "t"


def test_404_returns_empty():
    svc = _svc(_Resp(status_code=404))
    assert svc.list_bot_publishes("nope") == []


def test_non_404_http_error_raises():
    svc = _svc(_Resp(status_code=500))
    with pytest.raises(BaasServiceError):
        svc.list_bot_publishes("BOT-1")


def test_non_list_data_returns_empty():
    svc = _svc(_Resp(json_body={"code": 0, "data": {}}))
    assert svc.list_bot_publishes("BOT-1") == []
