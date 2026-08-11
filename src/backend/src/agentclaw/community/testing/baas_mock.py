"""Reusable respx mock for BaaS (``localhost:8890``) calls in unit tests.

Extracted from the former root ``tests/conftest.py`` so both subtree conftests
can install the same autouse behavior without a shared root conftest.

背景：BaasService 是单一 core service, 经注入的 HttpClient[baas] 真打
localhost:8890 — 这在 singlebox 联调 (``./scripts/local_setup.sh --local``) 下是对
的；但 CI 跑 pytest 时绝不能发网（没起 baas、没 hosts 解析）。这个 context manager
让任何一条 test 自动拿到 mock httpx：BaasService 全部方法默认返 success-shape 响应。

某条 test 想自定义 endpoint 行为：用 ``respx_mock`` fixture 在 test 内 add route 覆盖。

``respx`` / ``httpx`` are imported lazily inside :func:`mock_baas_calls` so importing
this module in a runtime without the dev deps never fails.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def mock_baas_calls() -> Iterator[Any]:
    """拦截单测进程内所有 httpx 调到 BaaS (localhost:8890) 的请求。

    只拦 ``localhost:8890``；其它 httpx（aicoding data-proxy / engine 不可达测试等）
    passthrough。Yields the active respx router so a test can add overriding routes.
    """
    import httpx
    import respx

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as r:
        # 关键：只拦 baas:8890，其它 httpx passthrough
        r.route(url__regex=r"^(?!http://localhost:8890).*").pass_through()

        # ws-info: LocalDeviceService._compose_device_conn_info 必调
        #
        # ``ws_url`` 按 BaaS 的拼法回显请求参数，而不是写死一个规范 URL：BaaS
        # 侧 ``build_proxypass_url`` 是 ``…/proxypass/{target}{path}`` 直接拼接，
        # 不补分隔符，所以 caller 少给前导斜杠时这里就该拼出坏 URL。写死规范值
        # 会把这类 caller 侧的拼接错误一路放到线上才暴露。
        def _ws_info_response(request: httpx.Request) -> httpx.Response:
            port = request.url.params.get("port", "20003")
            path = request.url.params.get("path", "/api/openclaw/ws")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "ws_url": f"ws://localhost:{port}{path}",
                        "token": "test_token",
                        "target": f"localhost:{port}",
                        "expires_at": "2099-12-31T00:00:00Z",
                    },
                },
            )

        r.get(url__regex=r"http://localhost:8890/api/v1/bots/.*/ws-info").mock(
            side_effect=_ws_info_response
        )

        # create_bot
        r.post(url__regex=r"http://localhost:8890/api/v1/bots$").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "bot_uuid": "test_bot_uuid",
                        "publish_id": 1,
                    },
                },
            )
        )

        # publish progress (BaasPublishPoller 后台轮询)
        r.get(url__regex=r"http://localhost:8890/api/v1/publishes/.*/progress").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "status": "ACTIVE",
                        "current_stage": "DONE",
                        "overall_progress": 100,
                        "stages": [],
                        "device_details": [],
                        "failed_devices": [],
                    },
                },
            )
        )

        # list devices
        r.get(url__regex=r"http://localhost:8890/api/v1/bots/.*/devices").mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "data": []},
            )
        )

        # 兜底：所有其它 baas:8890 endpoint 返 success-shape 空响应
        # (注意 url__regex 精确到 8890,避免误伤 aicoding data-proxy 等用 localhost 其它端口的测试)
        r.route(url__regex=r"http://localhost:8890/.*").mock(
            return_value=httpx.Response(200, json={"code": 0, "data": {}})
        )

        yield r
