"""TestHttpClientModule -- pytest vs singlebox HttpClient 分流。

被测对象: ``baas_http_client`` / ``bcn_http_client`` / ``general_http_client``
provider 内的 ``SERVER_ENV=singlebox`` 分流逻辑（B1 Task 10 从
``TestingInfrastructureModule`` 拆到 ``infrastructure/test/http_client.py``）。

为什么需要单测: pytest 默认进程不带 ``SERVER_ENV=singlebox``,所以日常单测只跑
``LocalHttpClient`` 分支; singlebox 分支 (返 ``HttpxClient`` 真 httpx) 在 CI
里只有显式构造 env 才能命中。本文件用 monkeypatch 把两条分支都覆盖到。

不通过 injector 整体 build (会拉起整个 prod 依赖栈),直接调 provider 方法验返回类型,
保持单测窄、快。
"""
from __future__ import annotations

import pytest

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules.infrastructure.test.http_client import TestHttpClientModule


@pytest.fixture
def module() -> TestHttpClientModule:
    return TestHttpClientModule()


@pytest.fixture
def baas_cfg() -> cfg.BaasConfig:
    # api_base_url 用一个跟默认不同的值,断言确实透传进去
    return cfg.BaasConfig(api_base_url="http://localhost:18890")


@pytest.fixture
def bcn_cfg() -> cfg.BcnConfig:
    # base_url 用一个跟默认不同的值,断言确实透传进去
    return cfg.BcnConfig(
        base_url="http://localhost:18891",
        base_url_pre="http://localhost:18892",
    )


def test_baas_http_client_singlebox_returns_real_httpx(
    module, baas_cfg, monkeypatch
):
    """SERVER_ENV=singlebox → HttpxClient(base_url=baas.api_base_url) (真 httpx)。"""
    monkeypatch.setenv("SERVER_ENV", "singlebox")
    from agentclaw.community.plugins.http_client import HttpxClient

    client = module.baas_http_client(baas_cfg)

    assert isinstance(client, HttpxClient)
    # HttpxClient 用 _base_url (私有属性,见 plugins/prod/http_client.py)
    assert client._base_url == "http://localhost:18890"


def test_baas_http_client_pytest_returns_local_mock(
    module, baas_cfg, monkeypatch
):
    """SERVER_ENV 非 singlebox (含未设) → LocalHttpClient (mock,unstubbed 抛错)。"""
    monkeypatch.delenv("SERVER_ENV", raising=False)
    from agentclaw.community.plugins.local.http_client import LocalHttpClient

    client = module.baas_http_client(baas_cfg)

    assert isinstance(client, LocalHttpClient)


def test_baas_http_client_singlebox_case_insensitive(
    module, baas_cfg, monkeypatch
):
    """SERVER_ENV 大小写不敏感: ``SINGLEBOX`` 也走真 httpx (provider 内 .lower())。"""
    monkeypatch.setenv("SERVER_ENV", "SINGLEBOX")
    from agentclaw.community.plugins.http_client import HttpxClient

    client = module.baas_http_client(baas_cfg)

    assert isinstance(client, HttpxClient)


def test_baas_http_client_other_env_returns_local_mock(
    module, baas_cfg, monkeypatch
):
    """SERVER_ENV=dev / prod / 任何非 singlebox 值 → 仍然走 mock。"""
    monkeypatch.setenv("SERVER_ENV", "dev")
    from agentclaw.community.plugins.local.http_client import LocalHttpClient

    client = module.baas_http_client(baas_cfg)

    assert isinstance(client, LocalHttpClient)


def test_bcn_http_client_singlebox_returns_real_httpx(module, bcn_cfg, monkeypatch):
    """SERVER_ENV=singlebox → HttpxClient(base_url=bcn.base_url) (真 httpx)。"""
    monkeypatch.setenv("SERVER_ENV", "singlebox")
    monkeypatch.setattr(
        "agentclaw.community.di.modules.infrastructure.test.http_client.get_current_env",
        lambda: "prod",
    )
    from agentclaw.community.plugins.http_client import HttpxClient

    client = module.bcn_http_client(bcn_cfg)

    assert isinstance(client, HttpxClient)
    assert client._base_url == "http://localhost:18891"


def test_bcn_http_client_singlebox_pre_uses_pre_base_url(
    module, bcn_cfg, monkeypatch
):
    """SERVER_ENV=singlebox + env=pre → HttpxClient(base_url=bcn.base_url_pre)."""
    monkeypatch.setenv("SERVER_ENV", "singlebox")
    monkeypatch.setattr(
        "agentclaw.community.di.modules.infrastructure.test.http_client.get_current_env",
        lambda: "pre",
    )
    from agentclaw.community.plugins.http_client import HttpxClient

    client = module.bcn_http_client(bcn_cfg)

    assert isinstance(client, HttpxClient)
    assert client._base_url == "http://localhost:18892"


def test_bcn_http_client_pytest_returns_local_mock(module, bcn_cfg, monkeypatch):
    """SERVER_ENV 未设 → LocalHttpClient mock (base_url=8891)。"""
    monkeypatch.delenv("SERVER_ENV", raising=False)
    from agentclaw.community.plugins.local.http_client import LocalHttpClient

    client = module.bcn_http_client(bcn_cfg)

    assert isinstance(client, LocalHttpClient)


def test_general_http_client_singlebox_returns_real_httpx(module, monkeypatch):
    """SERVER_ENV=singlebox → HttpxClient(base_url='') (真 httpx, 传全量 URL)。"""
    monkeypatch.setenv("SERVER_ENV", "singlebox")
    from agentclaw.community.plugins.http_client import HttpxClient

    client = module.general_http_client()

    assert isinstance(client, HttpxClient)


def test_general_http_client_pytest_returns_local_mock(module, monkeypatch):
    """SERVER_ENV 未设 → LocalHttpClient (base_url='')。"""
    monkeypatch.delenv("SERVER_ENV", raising=False)
    from agentclaw.community.plugins.local.http_client import LocalHttpClient

    client = module.general_http_client()

    assert isinstance(client, LocalHttpClient)
