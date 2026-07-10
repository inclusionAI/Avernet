"""HttpClientModule — general_http_client provider 冒烟测试。

只测 general_http_client provider（DI 装配），不整体构建 injector（prod 依赖栈
太重）。直接实例化 module 调 provider 方法即可。

注意：baas/bcn/general/masa http clients 是 profile-无关的，合并到中立的
``di/modules/http_client_module.py`` 的 ``HttpClientModule``（B9 收口 —— corp
与 community 的 http-client module 无 profile-specific 依赖，故合一，装在 base
module 列表；只有 test/corp_test 由 ``TestHttpClientModule`` 覆盖，singlebox
刻意消费这个真实 HTTP client binding）。
"""
from __future__ import annotations

import pytest

from agentclaw.community.di.modules.http_client_module import HttpClientModule


@pytest.fixture
def module() -> HttpClientModule:
    return HttpClientModule()


def test_general_http_client_provider_returns_httpx_client(module):
    """HttpClientModule.general_http_client() → HttpxClient(base_url='')。

    DI smoke: 验证 import + log + return 三行都能执行，返回正确类型。
    """
    from agentclaw.community.plugins.http_client import HttpxClient

    client = module.general_http_client()

    assert isinstance(client, HttpxClient)
