"""Endpoint tests for POST /api/v1/yuque/verify.

Cases cover the full branch matrix in
``adapters/http/yuque/router.py::verify_yuque_binding``:

- ``ok_bound`` — upstream 200 + ``login`` matches URL first segment.
- ``ok_not_bound`` — upstream 200 + ``login`` mismatch.
- ``ok_data_wrapper`` — upstream wraps user under ``data`` key.
- ``err_empty_namespace`` — URL has no first path segment.
- ``err_upstream_status`` — upstream returns non-200.
- ``err_transport`` — http_client raises ``httpx.HTTPError``.
"""
from __future__ import annotations

from typing import Annotated

import httpx

from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_GENERAL
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


def _general_http(world):
    # yuque.user_api comes from application-dev.yaml (the test profile's overlay),
    # so the injected YuqueConfig is populated — no per-test env needed.
    return world.get(Annotated[HttpClient, QUALIFIER_GENERAL])


def _resp(status: int, body):
    return httpx.Response(
        status_code=status,
        json=body,
        request=httpx.Request("GET", "https://yuque-api.antfin-inc.com/api/v2/user"),
    )


def _seed_login_match(world) -> None:
    _general_http(world).set_response("get", _resp(200, {"login": "aixcoding"}))


def _seed_login_mismatch(world) -> None:
    _general_http(world).set_response("get", _resp(200, {"login": "other_user"}))


def _seed_login_wrapped(world) -> None:
    # 语雀真实返回常用 `{"data": {"login": ...}}` 包裹
    _general_http(world).set_response("get", _resp(200, {"data": {"login": "aixcoding"}}))


def _seed_upstream_401(world) -> None:
    _general_http(world).set_response("get", _resp(401, {"message": "Unauthorized"}))


def _seed_transport_error(world) -> None:
    def _raise(path, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", path))

    _general_http(world).set_override("get", _raise)


@endpoint_test(
    method="POST",
    path="/api/v1/yuque/verify",
    scenario="ok_bound",
    seed=_seed_login_match,
    input=CaseInput(
        json_body={"url": "https://yuque.antfin-inc.com/aixcoding/tech", "team_token": "t"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"bound": True, "login": "aixcoding", "namespace": "aixcoding"},
        },
    ),
)
def verify_ok_bound():
    """login == 第一层 namespace → bound=True."""


@endpoint_test(
    method="POST",
    path="/api/v1/yuque/verify",
    scenario="ok_not_bound",
    seed=_seed_login_mismatch,
    input=CaseInput(
        json_body={"url": "https://yuque.antfin-inc.com/aixcoding/tech", "team_token": "t"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"bound": False, "login": "other_user", "namespace": "aixcoding"},
        },
    ),
)
def verify_ok_not_bound():
    """login != 第一层 namespace → bound=False（仍然 success=True）。"""


@endpoint_test(
    method="POST",
    path="/api/v1/yuque/verify",
    scenario="ok_data_wrapper",
    seed=_seed_login_wrapped,
    input=CaseInput(
        json_body={"url": "https://yuque.antfin-inc.com/aixcoding/tech", "team_token": "t"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"bound": True, "login": "aixcoding", "namespace": "aixcoding"},
        },
    ),
)
def verify_ok_data_wrapper():
    """upstream {data: {login: ...}} 也能正确解析。"""


@endpoint_test(
    method="POST",
    path="/api/v1/yuque/verify",
    scenario="err_empty_namespace",
    input=CaseInput(
        json_body={"url": "https://yuque.antfin-inc.com/", "team_token": "t"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": False, "error": "URL 缺少第一层路径"},
    ),
)
def verify_err_empty_namespace():
    """URL 没有 path → success=False，且不会真去打语雀。"""


@endpoint_test(
    method="POST",
    path="/api/v1/yuque/verify",
    scenario="err_upstream_status",
    seed=_seed_upstream_401,
    input=CaseInput(
        json_body={"url": "https://yuque.antfin-inc.com/aixcoding/tech", "team_token": "bad"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": False},
    ),
)
def verify_err_upstream_status():
    """upstream 401 → success=False，error 含状态码。"""


@endpoint_test(
    method="POST",
    path="/api/v1/yuque/verify",
    scenario="err_transport",
    seed=_seed_transport_error,
    input=CaseInput(
        json_body={"url": "https://yuque.antfin-inc.com/aixcoding/tech", "team_token": "t"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": False},
    ),
)
def verify_err_transport():
    """transport 抛 httpx.HTTPError → success=False。"""


@endpoint_test(
    method="POST",
    path="/api/v1/yuque/verify",
    scenario="err_missing_field",
    input=CaseInput(
        json_body={"url": "https://yuque.antfin-inc.com/aixcoding/tech"},
    ),
    expect=ExpectError(status=422),
)
def verify_err_missing_field():
    """缺 team_token → FastAPI 422 validation error。"""
